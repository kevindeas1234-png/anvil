; ANVIL - kernel entry: 16-bit trampoline, then 32-bit C.
;
; Runs at 0x10000 in real mode (stage 1 jumps here before any protected-mode
; switch). It collects the E820 memory map, writes the boot_info block, loads
; a flat GDT, enters protected mode and calls kmain().
;
; This mirrors the milestone-2/3 asm kernel, where the BIOS call worked.
;
; Beacons on COM1, one letter per phase:
;   A = stage 2 running      B = E820 collected
;   C = boot_info written    D = about to enter protected mode
;   E = 32-bit C entry       (from below)
[BITS 16]
section .text.entry
global _start
extern kmain

KERNEL_BASE equ 0x10000           ; where this image is linked
BOOT_INFO   equ 0x7000
E820_BUF    equ 0x8000
E820_MAX    equ 32

_start:
    cli
    mov ax, 0x1000
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0xF000
    ; Interrupts stay OFF through the BIOS calls. The milestone-2/3 kernel
    ; did the same: a timer IRQ landing inside SeaBIOS's E820 handler is
    ; re-entrant and hangs the machine. kmain enables them before the ticks.

    call serial_init16
    mov al, 'A'
    call beacon16

    ; ---- memory map ------------------------------------------------------
    ; Real BIOS E820 first, with interrupts off as in the milestone-2/3
    ; kernel. The call is bounded so a misbehaving BIOS cannot spin here.
    xor ebx, ebx
    xor bp, bp
.e820_loop:
    mov ax, bp
    mov cx, 24
    mul cx
    mov di, E820_BUF
    add di, ax
    xor ax, ax
    mov es, ax
    mov eax, 0xE820
    mov edx, 0x534D4150            ; 'SMAP'
    mov ecx, 24
    int 0x15
    jc .e820_done
    cmp eax, 0x534D4150
    jne .e820_done
    inc bp
    cmp bp, E820_MAX
    jae .e820_done
    test ebx, ebx
    jnz .e820_loop
.e820_done:
    test bp, bp
    jnz .have_map

    ; ---- fallback: two entries from the BIOS data area and CMOS ----------
    xor ax, ax
    mov es, ax
    mov di, E820_BUF
    mov dword [es:di + 0], 0
    mov dword [es:di + 4], 0
    movzx eax, word [es:0x0413]    ; KB of conventional memory
    shl eax, 10
    mov [es:di + 8], eax
    mov dword [es:di + 12], 0
    mov dword [es:di + 16], 1
    mov dword [es:di + 20], 0

    mov al, 0x30                   ; CMOS: extended KB, low byte
    or al, 0x80
    out 0x70, al
    in al, 0x71
    mov bl, al
    mov al, 0x31
    or al, 0x80
    out 0x70, al
    in al, 0x71
    mov bh, al

    mov di, E820_BUF + 24
    mov dword [es:di + 0], 0x00100000
    mov dword [es:di + 4], 0
    movzx eax, bx
    shl eax, 10
    mov [es:di + 8], eax
    mov dword [es:di + 12], 0
    mov dword [es:di + 16], 1
    mov dword [es:di + 20], 0
    mov bp, 2

.have_map:
    mov ax, 0x1000                 ; restore our data segment
    mov es, ax

    mov al, 'B'
    call beacon16

    ; ---- boot_info at 0x7000 ---------------------------------------------
    xor ax, ax
    mov es, ax
    mov di, BOOT_INFO
    movzx eax, bp
    mov [es:di + 0], eax           ; entry count
    mov dword [es:di + 4], E820_BUF
    movzx eax, word [es:0x0413]    ; conventional KB from the BIOS data area
    mov [es:di + 8], eax

    mov al, 'C'
    call beacon16

    ; ---- protected mode ---------------------------------------------------
    ; Build the GDT descriptor at 0x6000 from constants only. A 16-bit
    ; operand cannot carry the linked 0x10xxx address, and a section-relative
    ; difference is the one form NASM resolves without a relocation.
    xor ax, ax
    mov ds, ax
    mov word [0x6000], gdt_end - gdt_start - 1
    mov eax, KERNEL_BASE + (gdt_start - $$)
    mov dword [0x6002], eax
    lgdt [0x6000]

    mov al, 'D'
    call beacon16

    mov eax, cr0
    or eax, 1                      ; CR0.PE
    mov cr0, eax
    mov eax, BOOT_INFO
    jmp dword 0x08:pm32

; --- real-mode serial ----------------------------------------------------
serial_init16:
    mov dx, 0x3F8 + 1
    xor al, al
    out dx, al
    mov dx, 0x3F8 + 3
    mov al, 0x80
    out dx, al
    mov dx, 0x3F8 + 0
    mov al, 1
    out dx, al
    mov dx, 0x3F8 + 1
    xor al, al
    out dx, al
    mov dx, 0x3F8 + 3
    mov al, 0x03
    out dx, al
    mov dx, 0x3F8 + 2
    mov al, 0xC7
    out dx, al
    ret

beacon16:
    push ax
    push dx
    mov ah, al
    call .put
    mov ah, 13
    call .put
    mov ah, 10
    call .put                  ; every character goes through .put; no
    pop dx                     ; fall-through, so the stack stays balanced
    pop ax                     ; and ret returns to the real caller
    ret
.put:
    mov dx, 0x3FD
.wait_tx:
    in al, dx
    test al, 0x20
    jz .wait_tx
    mov dx, 0x3F8
    mov al, ah
    out dx, al
    ret

; --- 32-bit ---------------------------------------------------------------
[BITS 32]
pm32:
    mov esi, eax                   ; keep boot_info before AX is reloaded
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov fs, ax
    mov gs, ax
    mov ss, ax
    mov esp, 0x90000

    mov dx, 0x3F8                  ; beacon E: reached 32-bit entry
    mov al, 'E'
    out dx, al
    mov al, 13
    out dx, al
    mov al, 10
    out dx, al

    push esi                       ; boot_info -> kmain(boot_info)
    call kmain
    add esp, 4

.hang:
    cli
    hlt
    jmp .hang

; --- data ----------------------------------------------------------------
align 8
gdt_start:
    dq 0x0000000000000000
gdt_code:
    dq 0x00CF9A000000FFFF
gdt_data:
    dq 0x00CF92000000FFFF
gdt_end:

; (the LGDT descriptor is built at run time at 0x6000 - see above)
