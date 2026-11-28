; ANVIL - stage 1 boot sector.
;
; Loads the kernel from LBA 1 upwards. Two paths, because one image has to boot
; from a floppy, a USB stick and a hard disk:
;
;   L  INT 13h extensions (AH=42h) - addresses by LBA, so it does not care
;      what the device is
;   C  the old CHS arithmetic (18 sectors/track, 2 heads) - a floppy BIOS may
;      not offer extensions at all, so this stays as a fallback
;
; Whichever path is taken is announced on COM1 as a single character before any
; other output, so a log says how it booted rather than leaving it to be
; assumed. State lives in memory rather than registers because BIOS disk calls
; are not required to preserve anything except AX and the flags.
[BITS 16]
[ORG 0x7C00]

KERNEL_SECTORS equ 64              ; 32 KB of kernel space
CHUNK_SECTORS  equ 16              ; per AH=42h call: 8 KB, never crossing a segment
CHUNKS         equ KERNEL_SECTORS / CHUNK_SECTORS

start:
    cli
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0x7C00
    sti

    mov [boot_drive], dl
    mov al, 'B'                     ; entered the boot sector at all
    call mark_screen
    call serial_init
    mov al, 'S'
    call mark_screen

    ; Where is the kernel? On a floppy or a superfloppy it is at LBA 1. On a
    ; partitioned disk it starts at the first sector of partition 1 - and some
    ; firmware will not boot the device at all unless that table is valid.
    ; Read the table we were booted with instead of assuming one layout.
    mov al, [0x7C00 + 446]          ; partition 1 status
    test al, al
    jz .no_partition
    cmp byte [0x7C00 + 450], 0      ; partition 1 type
    je .no_partition
    mov eax, [0x7C00 + 454]         ; partition 1 first LBA
    test eax, eax
    jz .no_partition
    mov [kernel_base], eax
.no_partition:

    ; --- does this drive offer extended (LBA) reads? ---------------------
    mov ah, 0x41
    mov bx, 0x55AA
    mov dl, [boot_drive]
    int 0x13
    jc .fallback
    cmp bx, 0xAA55
    jne .fallback

    mov al, 'L'
    call mark
    mov al, 10
    call mark
    jmp lba_read

.fallback:
    mov al, 'C'
    call mark
    mov al, 10
    call mark

; ---------------------------------------------------------------------------
; CHS path: one sector at a time, as before. Kept for drives whose BIOS has no
; LBA extensions - notably some floppy controllers.
; ---------------------------------------------------------------------------
chs_read:
    mov word [read_index], 0
.read_loop:
    ; destination = 0x10000 + index * 512 -> ES = 0x1000 + index * 32
    mov ax, [read_index]
    shl ax, 5
    add ax, 0x1000
    mov es, ax
    xor bx, bx

    ; LBA = index + 1 -> CHS (18 sectors/track, 2 heads)
    mov ax, [read_index]
    inc ax
    xor dx, dx
    mov cx, 18
    div cx                         ; ax = LBA / 18, dx = LBA % 18
    mov cl, dl
    inc cl                         ; sector number (1-based)
    xor dx, dx
    mov bx, 2
    div bx                         ; ax = cylinder, dx = head
    mov ch, al
    mov dh, dl
    mov dl, [boot_drive]
    xor bx, bx
    mov ax, 0x0201                 ; read one sector
    int 0x13
    jc disk_error

    inc word [read_index]
    mov ax, [read_index]
    cmp ax, KERNEL_SECTORS
    jb .read_loop
    jmp 0x1000:0x0000              ; real-mode hand-off

; ---------------------------------------------------------------------------
; LBA path: CHUNK_SECTORS at a time through a Disk Address Packet.
; Segment advances by 0x200 per chunk (16 sectors = 8 KB), which keeps every
; transfer inside one 64 KB window and never crossing a boundary.
; ---------------------------------------------------------------------------
lba_read:
    xor bp, bp                     ; chunk index
.next_chunk:
    mov ax, bp
    shl ax, 9                      ; chunk * 512 == chunk * 0x200
    add ax, 0x1000
    mov [dap_seg], ax

    movzx eax, bp
    shl eax, 4                     ; chunk * 16 sectors
    add eax, [kernel_base]         ; 1 on a floppy, the partition start on a disk
    mov [dap_lba], eax
    mov dword [dap_lba + 4], 0

    mov word [dap_count], CHUNK_SECTORS
    mov word [dap_off], 0

    mov byte [retries], 3
.retry:
    mov si, dap
    mov ah, 0x42
    mov dl, [boot_drive]
    int 0x13
    jnc .chunk_ok
    dec byte [retries]
    jnz .retry
    jmp disk_error
.chunk_ok:
    inc bp
    mov al, '.'                     ; one dot per 16-sector chunk loaded
    call mark_screen
    cmp bp, CHUNKS
    jb .next_chunk
    mov al, 'J'                     ; about to hand control to the kernel
    call mark_screen
    jmp 0x1000:0x0000

; ---------------------------------------------------------------------------
disk_error:
    ; Screen first. The serial line is optional; the screen is the only channel
    ; guaranteed to exist, and an error nobody can read is not an error message.
    mov si, msg_disk_error
.screen:
    lodsb
    test al, al
    jz .wire
    mov ah, 0x0E
    mov bx, 0x0007
    int 0x10
    jmp .screen
.wire:
    mov si, msg_disk_error
    call serial_puts
.hang:
    hlt
    jmp .hang

; --- serial (COM1) ---------------------------------------------------------
; The UART is not guaranteed to be initialised by this point in POST, so it is
; set up here rather than assuming the firmware left it usable.
serial_init:
    mov dx, 0x3F9
    xor al, al
    out dx, al                     ; no interrupts
    mov dx, 0x3FB
    mov al, 0x80
    out dx, al                     ; DLAB on
    mov dx, 0x3F8
    mov al, 1
    out dx, al                     ; divisor low: 115200
    mov dx, 0x3F9
    xor al, al
    out dx, al                     ; divisor high
    mov dx, 0x3FB
    mov al, 0x03
    out dx, al                     ; 8N1, DLAB off
    mov dx, 0x3FA
    mov al, 0xC7
    out dx, al                     ; FIFO on, cleared
    mov dx, 0x3FC
    mov al, 0x0B
    out dx, al                     ; DTR, RTS, OUT2
    ret

; Say it on the screen as well as the wire: on a machine with no COM port the
; serial markers are invisible, and an invisible boot loader cannot be tested.
mark:                              ; AL = character, screen and wire
    push ax
    mov ah, 0x0E                   ; screen FIRST: the BIOS cannot hang
    mov bx, 0x0007
    int 0x10
    pop ax
    push ax
    call serial_putc
    pop ax
    ret

; Progress markers go to the SCREEN ONLY. The serial log's first line is the
; boot-path marker and nothing else, so chatter here would break the one
; assertion that records how the kernel was loaded.
mark_screen:                       ; AL = character
    push ax
    mov ah, 0x0E
    mov bx, 0x0007
    int 0x10
    pop ax
    ret

serial_putc:                       ; AL = character
    push ax
    push dx
    push cx
    mov ah, al
    mov cx, 0x0FFF                 ; bounded: a board with no UART must not
    mov dx, 0x3FD                  ; hang here and be reset by a watchdog
.wait:
    in al, dx
    test al, 0x20                  ; transmit holding register empty?
    jnz .send
    loop .wait
    jmp .done                      ; no UART: give up quietly
.send:
    mov dx, 0x3F8
    mov al, ah
    out dx, al
.done:
    pop cx
    pop dx
    pop ax
    ret

serial_puts:                       ; SI = NUL-terminated string
    push ax
    push si
.next:
    lodsb
    test al, al
    jz .done
    call serial_putc
    jmp .next
.done:
    pop si
    pop ax
    ret

; --- data -----------------------------------------------------------------
boot_drive:     db 0
read_index:     dw 0
retries:        db 0
kernel_base:    dd 1               ; first kernel sector, read from partition 1

dap:
dap_size:       db 16
dap_reserved:   db 0
dap_count:      dw CHUNK_SECTORS
dap_off:        dw 0
dap_seg:        dw 0x1000
dap_lba:        dq 1

msg_disk_error: db "ANVIL: disk read failed", 13, 10, 0

times 510 - ($ - $$) db 0
dw 0xAA55
