; hello.asm - the smallest possible test that the firmware will run OUR code.
;
; 40 bytes of well-trodden path: set up segments, print one line with the BIOS
; teletype service, halt. No disk reads, no partition parsing, no protected mode.
; If this does not appear on screen, the firmware is not executing our boot
; sector at all, and nothing in stage1 can matter yet.
;
;   nasm -f bin -o build\hello.bin tools\hello.asm

        org 0x7C00
        bits 16

start:
        cli
        xor     ax, ax
        mov     ds, ax
        mov     es, ax
        mov     ss, ax
        mov     sp, 0x7C00
        sti

        mov     si, msg
.next:
        lodsb
        test    al, al
        jz      .hang
        mov     ah, 0x0E
        mov     bx, 0x0007
        int     0x10
        jmp     .next
.hang:
        hlt
        jmp     .hang

msg:    db 13, 10, "  ANVIL: hello from the boot sector", 13, 10
        db "  if you can read this, the firmware ran my code", 13, 10, 0

; --- partition table at 446 -------------------------------------------------
; A real entry, because some firmware refuses a device without one. The code
; above never reads it; it exists to satisfy the firmware's sanity check.
        times 446 - ($ - $$) db 0
        db 0x80, 0xFE, 0xFF, 0xFF, 0x0C, 0xFE, 0xFF, 0xFF
        dd 2048, 2048

        times 510 - ($ - $$) db 0
        dw 0xAA55
