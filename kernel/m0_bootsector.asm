; ---------------------------------------------------------------------------
; AOS - milestone 0 boot sector
;
; Runs in 16-bit real mode, straight out of the BIOS. Its only job is to prove
; the whole chain works: image -> firmware -> our code -> observable output.
;
;   * initialises COM1 at 115200 8N1 and prints a banner
;   * reports conventional memory via the BIOS
;   * exits QEMU through the isa-debug-exit device so CI terminates cleanly
;
; Build: nasm -f bin kernel\boot.asm -o images\aos.img
; Test : run_vm.bat   (sandboxed: -snapshot -nic none -display none)
; ---------------------------------------------------------------------------
[BITS 16]
[ORG 0x7C00]

SERIAL_PORT equ 0x3F8
EXIT_PORT   equ 0x501          ; supplied by run_vm.bat

start:
    cli
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0x7C00             ; stack grows down from just below us
    sti

    call serial_init

    mov si, msg_boot
    call puts

    int 0x12                   ; BIOS: ax = conventional memory in KB
    mov bx, ax
    mov si, msg_mem
    call puts
    mov ax, bx
    call put_dec
    mov si, msg_kb
    call puts

    mov si, msg_halt
    call puts

    mov dx, EXIT_PORT          ; tell QEMU we are done, so the test ends
    mov al, 0x10
    out dx, al

.hang:
    hlt
    jmp .hang

; --- COM1 ---------------------------------------------------------------
serial_init:
    mov dx, SERIAL_PORT + 1
    xor al, al
    out dx, al                 ; no UART interrupts
    mov dx, SERIAL_PORT + 3
    mov al, 0x80
    out dx, al                 ; enable divisor latch
    mov dx, SERIAL_PORT + 0
    mov al, 1
    out dx, al                 ; divisor 1 -> 115200 baud
    mov dx, SERIAL_PORT + 1
    xor al, al
    out dx, al
    mov dx, SERIAL_PORT + 3
    mov al, 0x03
    out dx, al                 ; 8N1, divisor latch off
    mov dx, SERIAL_PORT + 2
    mov al, 0xC7
    out dx, al                 ; enable + clear FIFOs
    ret

serial_putc:
    push ax
    push dx
    mov ah, al
.wait_tx:
    mov dx, SERIAL_PORT + 5
    in al, dx
    test al, 0x20              ; transmitter holding register empty?
    jz .wait_tx
    mov dx, SERIAL_PORT
    mov al, ah
    out dx, al
    pop dx
    pop ax
    ret

; --- console ------------------------------------------------------------
putc:
    push ax
    push bx
    call serial_putc
    mov ah, 0x0E               ; BIOS teletype
    mov bx, 0x0007
    int 0x10
    pop bx
    pop ax
    ret

puts:
    push ax
    push si
.next:
    lodsb
    test al, al
    jz .done
    call putc
    jmp .next
.done:
    pop si
    pop ax
    ret

put_dec:
    push ax
    push bx
    push cx
    push dx
    xor cx, cx
    mov bx, 10
.divide:
    xor dx, dx
    div bx
    push dx
    inc cx
    test ax, ax
    jnz .divide
.emit:
    pop ax
    add al, '0'
    call putc
    loop .emit
    pop dx
    pop cx
    pop bx
    pop ax
    ret

; --- data ---------------------------------------------------------------
msg_boot db "AOS KERNEL ONLINE", 13, 10, 0
msg_mem  db "memory: ", 0
msg_kb   db " KB conventional", 13, 10, 0
msg_halt db "AOS HALT", 13, 10, 0

; --- boot signature -----------------------------------------------------
times 510 - ($ - $$) db 0
dw 0xAA55

; Pad to a 1.44 MB floppy image so QEMU treats it as a bootable disk.
times 1474560 - ($ - $$) db 0
