; ANVIL - stage 1 boot sector.
;
; Loads the kernel one sector at a time with LBA -> CHS conversion, so the
; image can grow past a single 18-sector floppy track. State lives in memory
; rather than registers because BIOS disk calls are not required to preserve
; anything except AX and the flags.
[BITS 16]
[ORG 0x7C00]

KERNEL_SECTORS equ 32              ; 16 KB of kernel space

start:
    cli
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0x7C00
    sti

    mov [boot_drive], dl
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

disk_error:
    mov si, msg_disk_error
.next:
    lodsb
    test al, al
    jz .hang
    mov ah, 0x0E
    mov bx, 0x0007
    int 0x10
    jmp .next
.hang:
    hlt
    jmp .hang

boot_drive:     db 0
msg_disk_error: db "ANVIL: disk read failed", 13, 10, 0

read_index:     dw 0

times 510 - ($ - $$) db 0
dw 0xAA55
