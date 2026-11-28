; ANVIL - call the BIOS from 32-bit protected mode.
;
; There is no USB or AHCI driver in this kernel, but the firmware already has
; one. So: drop to real mode, ask the BIOS, come back. This is the most delicate
; code in the project, and it is deliberately tiny.
;
;   u32 bios_test(void)                                     1 = the switch works
;   u32 bios_disk_write(u32 drive, u32 lba, u32 count, u32 buf_phys)
;                                                           0 = ok, else 0xNN01
;
; Both go through one switching routine. The buffer must be below 1 MB and one
; sector at a time, because some firmware will not transfer across a 64 KB
; boundary.
[BITS 32]

%define LOW_BASE    0x3000              ; NOT 0x8000: that is entry.asm's E820 buffer,
                                        ; and putting the DAP there corrupted the memory
                                        ; map behind the kernel's own back.
%define LOW_DAP     (LOW_BASE + 0x00)   ; 16-byte disk address packet
%define LOW_GDTR    (LOW_BASE + 0x10)   ; saved 32-bit GDTR
%define LOW_IDTR    (LOW_BASE + 0x20)   ; saved IDTR
%define LOW_CR0     (LOW_BASE + 0x30)
%define LOW_ESP     (LOW_BASE + 0x34)
%define LOW_STATUS  (LOW_BASE + 0x38)
%define LOW_AH      (LOW_BASE + 0x39)
%define LOW_DRIVE   (LOW_BASE + 0x3C)
%define LOW_SEG     (LOW_BASE + 0x3E)
%define LOW_OFF     (LOW_BASE + 0x40)
%define LOW_OP      (LOW_BASE + 0x44)   ; 0 = disk write, 1 = teletype
%define LOW_CHAR    (LOW_BASE + 0x45)
%define LOW_RET     (LOW_BASE + 0x70)   ; continuation address
%define LOW_RMGDT   (LOW_BASE + 0x50)
%define LOW_RMGDTR  (LOW_BASE + 0x60)
%define LOW_STACK   0x7C00              ; below the kernel; the boot sector is done

; Breadcrumbs to QEMU's debug port. On metal these writes go nowhere harmless;
; in QEMU they land on stderr, which is the only way to see inside a mode switch
; that has no console to print to.
%macro TRACE 1
    mov     al, %1
    out     0xE9, al
%endmacro

section .text

; ---------------------------------------------------------------------------
global bios_test
bios_test:
    push    ebp
    mov     ebp, esp
    pushad
    pushfd
    cli
    mov     byte [LOW_OP], 1
    mov     byte [LOW_CHAR], 'X'
    mov     [LOW_ESP], esp              ; top of stack = the flags pushed above
    mov     dword [LOW_RET], .continue  ; jump, do not call: a `ret` here would
    jmp     bios_switch                 ; pop the flags as if they were an address
.continue:
    movzx   eax, byte [LOW_STATUS]      ; same convention as the disk call
    popfd
    popad
    leave
    ret

; ---------------------------------------------------------------------------
global bios_disk_write
bios_disk_write:
    push    ebp
    mov     ebp, esp
    pushad
    pushfd
    cli
    mov     byte [LOW_OP], 0

    mov     esi, [ebp + 8]              ; drive
    mov     ebx, [ebp + 12]             ; lba
    mov     ecx, [ebp + 16]             ; count
    mov     edi, [ebp + 20]             ; buffer, physical

    mov     byte [LOW_DAP + 0], 16
    mov     byte [LOW_DAP + 1], 0
    mov     [LOW_DAP + 2], cx
    mov     eax, edi
    and     eax, 0x0F
    mov     [LOW_DAP + 4], ax           ; offset in the paragraph
    mov     eax, edi
    shr     eax, 4
    mov     [LOW_DAP + 6], ax           ; segment
    mov     [LOW_DAP + 8], ebx
    mov     dword [LOW_DAP + 12], 0

    mov     [LOW_DRIVE], esi
    mov     [LOW_ESP], esp
    mov     dword [LOW_RET], .continue
    jmp     bios_switch
.continue:

    movzx   eax, byte [LOW_STATUS]
    test    eax, eax
    jz      .ok
    movzx   ebx, byte [LOW_AH]
    shl     ebx, 8
    or      eax, ebx                    ; 0xNN01: 1 = failed, NN = BIOS AH
.ok:
    popfd
    popad
    leave
    ret

; ---------------------------------------------------------------------------
; The switch itself. Not a subroutine in the normal sense: the stack below the
; return address is abandoned while real mode runs, then restored from LOW_ESP.
bios_switch:
    sgdt    [LOW_GDTR]
    sidt    [LOW_IDTR]
    mov     eax, cr0
    mov     [LOW_CR0], eax

    ; Where the REAL-MODE half lives, in real-mode terms. This must be real16,
    ; not rm16: rm16 is the protected-mode 16-bit entry, and jumping there after
    ; clearing PE loops forever (the first version did exactly that).
    mov     eax, real16
    mov     edx, eax
    shr     eax, 4
    mov     [LOW_SEG], ax
    and     edx, 0x0F
    mov     [LOW_OFF], dx

    ; A 16-bit GDT whose code descriptor's base IS rm16, so a far jump to
    ; offset 0 lands on it. A 32-bit far jump cannot address above 64 KB in a
    ; D=0 segment, which is why the base does the work.
    mov     dword [LOW_RMGDT + 0], 0
    mov     dword [LOW_RMGDT + 4], 0
    mov     eax, rm16
    mov     ebx, eax
    mov     word [LOW_RMGDT + 8], 0xFFFF
    mov     [LOW_RMGDT + 10], ax
    shr     ebx, 16
    mov     [LOW_RMGDT + 12], bl
    mov     byte [LOW_RMGDT + 13], 0x9A
    mov     byte [LOW_RMGDT + 14], 0x00
    mov     [LOW_RMGDT + 15], bh

    ; A 16-bit stack before anything 16-bit runs. The far jump below lands in a
    ; D=0 segment, where push/retf use SP - the low 16 bits of our 32-bit ESP,
    ; which pointed at 0x90000 and therefore wrapped to 0. That hang was the
    ; whole failure.
    mov     ax, 0x10                    ; kernel data selector, flat
    mov     ss, ax
    mov     esp, LOW_STACK

    TRACE   '1'
    mov     word [LOW_RMGDTR + 0], 15
    mov     dword [LOW_RMGDTR + 2], LOW_RMGDT
    lgdt    [LOW_RMGDTR]
    TRACE   '2'

    mov     eax, cr0
    and     eax, 0x7FFFFFFF             ; paging off
    mov     cr0, eax

    TRACE   '3'
    jmp     dword 0x08:0                ; 16-bit code at base rm16

; ---------------------------------------------------------------------------
[BITS 16]
rm16:
    TRACE   '4'
    mov     eax, cr0
    and     al, 0xFE                    ; PE off
    mov     cr0, eax

    ; CS still holds a selector, which real mode reads as segment 0x0008. Go to
    ; the computed real-mode address instead.
    mov     ax, [LOW_SEG]
    push    ax
    mov     ax, [LOW_OFF]
    push    ax
    retf

real16:
    TRACE   '5'
    xor     ax, ax
    mov     ds, ax
    mov     es, ax
    mov     ss, ax
    mov     sp, LOW_STACK

    ; Put the 8259s back the way the firmware left them. The kernel remaps them
    ; to 0x20-0x2F behind its own IDT; in real mode the CPU uses the IVT at
    ; zero, where those vectors mean nothing.
    mov     al, 0x11
    out     0x20, al
    out     0xA0, al
    mov     al, 0x08
    out     0x21, al
    mov     al, 0x70
    out     0xA1, al
    mov     al, 0x04
    out     0x21, al
    mov     al, 0x02
    out     0xA1, al
    mov     al, 0x01
    out     0x21, al
    out     0xA1, al
    mov     al, 0x00
    out     0x21, al
    out     0xA1, al

    mov     byte [LOW_STATUS], 1        ; assume failure until it says otherwise
    mov     byte [LOW_AH], 0xFE

    cmp     byte [LOW_OP], 0
    jne     .tele

    mov     dl, [LOW_DRIVE]
    mov     si, LOW_DAP
    mov     cx, [LOW_DAP + 2]
    mov     ax, 0x4300                  ; extended write, no verify
    sti
    int     0x13
    cli
    jmp     .after

.tele:
    sti
    mov     al, [LOW_CHAR]
    mov     ah, 0x0E
    mov     bx, 0x0007
    int     0x10
    cli
    ; The teletype service does not report through carry, so it cannot fail.
    mov     byte [LOW_STATUS], 0
    jmp     .done

.after:
    TRACE   '6'
    mov     byte [LOW_STATUS], 0
    jnc     .done
    mov     byte [LOW_STATUS], 1
    mov     [LOW_AH], ah
.done:
    mov     eax, cr0
    or      al, 1
    mov     cr0, eax
    ; The kernel's GDT before the far jump: selector 0x08 means 16-bit in the
    ; transitional table and 32-bit in ours.
    lgdt    [LOW_GDTR]
    jmp     dword 0x08:pm32

; ---------------------------------------------------------------------------
[BITS 32]
pm32:
    TRACE   '7'
    ; Reload the data segments FIRST. The 16-bit half set them to 0, and in
    ; protected mode selector 0 is the null descriptor - so the first stack
    ; access (even `ret`) would fault, and the fault handler needs a stack too.
    mov     ax, 0x10
    mov     ds, ax
    mov     es, ax
    mov     ss, ax
    mov     fs, ax
    mov     gs, ax

    mov     eax, [LOW_CR0]              ; paging back on, as it was
    mov     cr0, eax
    TRACE   '8'
    lidt    [LOW_IDTR]
    TRACE   '9'
    mov     esp, [LOW_ESP]
    TRACE   'a'
    jmp     [LOW_RET]                   ; back into the caller, stack as it was
