; ---------------------------------------------------------------------------
; AOS - milestone 2: firmware memory map + paging
;
; Builds on milestone 1 (protected mode + PIT interrupts) and adds:
;
;   * E820 memory map queried from the BIOS in real mode
;   * usable RAM summed and reported in MB
;   * a page directory with 4 MB pages (CR4.PSE), identity mapping the first
;     4 GB, plus one deliberately remapped window to prove translation works
;
; Build : nasm -f bin kernel\boot.asm -o images\aos.img
; Test  : run_vm.bat / test.bat
;
; Memory map
;   0x007C00 stage 1
;   0x008000 E820 buffer (up to 128 entries)
;   0x010000 stage 2 / kernel
;   0x090000 stack top
;   0x100000 page directory (4 KB, aligned)
; ---------------------------------------------------------------------------

KERNEL_SECTORS equ 17                  ; 17 * 512 = 8704 bytes
KERNEL_SIZE    equ KERNEL_SECTORS * 512
IMAGE_SIZE     equ 1474560             ; 1.44 MB floppy
EXIT_PORT      equ 0x501

E820_BUFFER    equ 0x8000
E820_MAX       equ 128
PAGE_DIR       equ 0x100000

; ===========================================================================
; Stage 1 - boot sector
; ===========================================================================
section .boot vstart=0x7C00
[BITS 16]

stage1:
    cli
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0x7C00
    sti

    mov [boot_drive], dl

    mov ax, 0x1000
    mov es, ax
    xor bx, bx
    mov ah, 0x02
    mov al, KERNEL_SECTORS
    mov ch, 0
    mov cl, 2
    mov dh, 0
    mov dl, [boot_drive]
    int 0x13
    jc disk_error

    jmp 0x1000:0x0000

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
msg_disk_error: db "AOS: disk read failed", 13, 10, 0

times 510 - ($ - $$) db 0
dw 0xAA55

; ===========================================================================
; Stage 2 - the kernel
; ===========================================================================
section .kernel vstart=0x10000
[BITS 16]

kernel16:
    cli
    mov ax, 0x1000
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0xF000

    call serial_init

    mov si, msg_stage2
    call puts16

    int 0x12                       ; conventional memory in KB
    mov [mem_kb], ax

    call query_e820

    lgdt [gdt_descriptor]

    mov si, msg_gdt
    call puts16

    mov eax, cr0
    or eax, 1
    mov cr0, eax
    jmp dword 0x08:pm32

; --- E820 memory map ----------------------------------------------------
query_e820:
    xor ebx, ebx
    xor bp, bp
.loop:
    mov ax, bp
    mov cx, 24
    mul cx                         ; ax = entry offset (bp * 24)
    mov di, E820_BUFFER
    add di, ax
    mov ax, 0
    mov es, ax
    mov eax, 0xE820
    mov edx, 0x534D4150            ; 'SMAP'
    mov ecx, 24
    int 0x15
    jc .done
    cmp eax, 0x534D4150
    jne .done
    inc bp
    cmp bp, E820_MAX
    jae .done
    test ebx, ebx
    jnz .loop
.done:
    mov [e820_count], bp
    ret

; --- 16-bit serial ------------------------------------------------------
serial_init:
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

serial_putc16:
    push ax
    push dx
    mov ah, al
.wait_tx:
    mov dx, 0x3FD
    in al, dx
    test al, 0x20
    jz .wait_tx
    mov dx, 0x3F8
    mov al, ah
    out dx, al
    pop dx
    pop ax
    ret

puts16:
    push ax
    push si
.next:
    lodsb
    test al, al
    jz .done
    call serial_putc16
    jmp .next
.done:
    pop si
    pop ax
    ret

; ---------------------------------------------------------------------------
; 32-bit protected mode
; ---------------------------------------------------------------------------
[BITS 32]
pm32:
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov fs, ax
    mov gs, ax
    mov ss, ax
    mov esp, 0x90000

    mov esi, msg_pmode
    call puts32

    call idt_install
    mov esi, msg_idt
    call puts32

    call pic_remap
    mov esi, msg_pic
    call puts32

    call pit_start
    mov esi, msg_pit
    call puts32

    mov esi, msg_boot
    call puts32

    mov esi, msg_mem
    call puts32
    movzx eax, word [mem_kb]
    call put_dec32
    mov esi, msg_kb
    call puts32

    mov esi, msg_mode
    call puts32

    call report_e820
    call enable_paging

    sti

.idle:
    hlt
    cmp dword [test_done], 0
    je .idle
    jmp fault_test

; --- deliberate fault, to prove the exception path is live ----------------
fault_test:
    mov esi, msg_fault
    call puts32
    ud2                            ; #UD, vector 6

; --- E820 summary -------------------------------------------------------
report_e820:
    mov esi, msg_e820
    call puts32

    movzx eax, word [e820_count]
    call put_dec32

    mov esi, msg_entries
    call puts32

    ; sum the low 32 bits of every type-1 (usable) region
    mov esi, E820_BUFFER
    xor ebx, ebx
    movzx ecx, word [e820_count]
    test ecx, ecx
    jz .print_mb
.sum:
    cmp dword [esi + 16], 1
    jne .next
    add ebx, [esi + 8]
.next:
    add esi, 24
    loop .sum

.print_mb:
    mov eax, ebx
    shr eax, 20                    ; bytes -> MB
    call put_dec32
    mov esi, msg_mb
    call puts32
    ret

; --- paging -------------------------------------------------------------
enable_paging:
    ; zero the page directory (string ops must run forward)
    cld
    mov edi, PAGE_DIR
    xor eax, eax
    mov ecx, 1024
    rep stosd

    ; identity map the first 4 GB with 4 MB pages (P=1, RW=1, PS=1)
    mov edi, PAGE_DIR
    mov eax, 0x00000083
    mov [edi + 0], eax
    add eax, 0x00400000
    mov [edi + 4], eax
    add eax, 0x00400000
    mov [edi + 8], eax
    add eax, 0x00400000
    mov [edi + 12], eax

    ; remap window: virtual 0xC0000000 -> physical 0
    mov dword [edi + 768*4], 0x00000083

    mov eax, cr4
    or eax, 1 << 4                 ; CR4.PSE
    mov cr4, eax

    mov eax, PAGE_DIR
    mov cr3, eax

    mov eax, cr0
    or eax, 0x80000000             ; CR0.PG
    mov cr0, eax
    jmp short .flushed
.flushed:
    mov esi, msg_paging
    call puts32

    ; prove translation: the same physical byte via two virtual addresses
    mov dword [0xC0200000], 0xA05A05A0
    mov eax, [0x00200000]
    cmp eax, 0xA05A05A0
    jne .failed

    mov esi, msg_pageok
    call puts32
    ret
.failed:
    mov esi, msg_pagefail
    call puts32
    ret

; --- IDT ---------------------------------------------------------------
idt_install:
    ; vectors 0x00..0x1F from the exception stub table
    mov edi, idt
    mov esi, isr_table
    xor ebx, ebx
.fill:
    mov eax, [esi + ebx*4]
    mov [edi + ebx*8 + 0], ax
    shr eax, 16
    mov [edi + ebx*8 + 6], ax
    mov word [edi + ebx*8 + 2], 0x08
    mov byte [edi + ebx*8 + 4], 0
    mov byte [edi + ebx*8 + 5], 0x8E
    inc ebx
    cmp ebx, 32
    jb .fill

    mov eax, irq0_stub
    mov word [idt + 0x20*8 + 0], ax
    mov word [idt + 0x20*8 + 2], 0x08
    mov byte [idt + 0x20*8 + 4], 0
    mov byte [idt + 0x20*8 + 5], 0x8E
    shr eax, 16
    mov word [idt + 0x20*8 + 6], ax

    mov eax, idt
    mov [idtr + 2], eax
    mov word [idtr], 256*8 - 1
    lidt [idtr]
    ret

; --- 8259 PIC ----------------------------------------------------------
pic_remap:
    mov al, 0x11
    out 0x20, al
    out 0xA0, al
    mov al, 0x20
    out 0x21, al
    mov al, 0x28
    out 0xA1, al
    mov al, 0x04
    out 0x21, al
    mov al, 0x02
    out 0xA1, al
    mov al, 0x01
    out 0x21, al
    out 0xA1, al
    mov al, 0xFE
    out 0x21, al
    mov al, 0xFF
    out 0xA1, al
    ret

; --- PIT ---------------------------------------------------------------
pit_start:
    mov al, 0x36
    out 0x43, al
    mov ax, 11932                  ; ~100 Hz
    out 0x40, al
    mov al, ah
    out 0x40, al
    ret

; --- IRQ0 handler ------------------------------------------------------
irq0_stub:
    pushad

    inc dword [tick_count]
    mov eax, [tick_count]

    cmp eax, 10
    ja .finished

    mov esi, msg_tick
    call puts32
    mov eax, [tick_count]
    call put_dec32
    mov esi, msg_crlf
    call puts32
    jmp .eoi

.finished:
    cmp eax, 11
    jne .eoi
    mov esi, msg_done
    call puts32
    mov dword [test_done], 1       ; main loop takes over from here

.eoi:
    mov al, 0x20
    out 0x20, al
    popad
    iretd

; --- exception stubs ----------------------------------------------------
; Each stub pushes a vector number (and a dummy error code where the CPU does
; not supply one), so every exception reaches isr_common with an identical
; stack layout: vector, error, eip, cs, eflags.
isr_common:
    pushad
    mov esi, msg_exc
    call puts32
    mov eax, [esp + 32]            ; vector
    call put_hex32
    mov esi, msg_eip
    call puts32
    mov eax, [esp + 40]            ; eip
    call put_hex32
    mov esi, msg_err
    call puts32
    mov eax, [esp + 36]            ; error code
    call put_hex32
    mov esi, msg_handled
    call puts32
    mov dx, EXIT_PORT
    mov al, 0x10
    out dx, al
.hang:
    cli
    hlt
    jmp .hang

%macro ISR_NOERR 1
isr%1:
    push dword 0
    push dword %1
    jmp isr_common
%endmacro

%macro ISR_ERR 1
isr%1:
    push dword %1
    jmp isr_common
%endmacro

ISR_NOERR 0
ISR_NOERR 1
ISR_NOERR 2
ISR_NOERR 3
ISR_NOERR 4
ISR_NOERR 5
ISR_NOERR 6
ISR_NOERR 7
ISR_ERR   8
ISR_NOERR 9
ISR_ERR   10
ISR_ERR   11
ISR_ERR   12
ISR_ERR   13
ISR_ERR   14
ISR_NOERR 15
ISR_NOERR 16
ISR_ERR   17
ISR_NOERR 18
ISR_NOERR 19
ISR_NOERR 20
ISR_ERR   21
ISR_NOERR 22
ISR_NOERR 23
ISR_NOERR 24
ISR_NOERR 25
ISR_NOERR 26
ISR_NOERR 27
ISR_NOERR 28
ISR_NOERR 29
ISR_NOERR 30
ISR_NOERR 31

isr_table:
    dd isr0,  isr1,  isr2,  isr3,  isr4,  isr5,  isr6,  isr7
    dd isr8,  isr9,  isr10, isr11, isr12, isr13, isr14, isr15
    dd isr16, isr17, isr18, isr19, isr20, isr21, isr22, isr23
    dd isr24, isr25, isr26, isr27, isr28, isr29, isr30, isr31

put_hex32:
    push eax
    push ebx
    push ecx
    mov ebx, eax
    mov ecx, 8
.rotate:
    rol ebx, 4
    mov eax, ebx
    and eax, 0x0F
    cmp al, 10
    jb .digit
    add al, 'a' - 10
    jmp .emit
.digit:
    add al, '0'
.emit:
    call serial_putc32
    loop .rotate
    pop ecx
    pop ebx
    pop eax
    ret

; --- 32-bit serial ------------------------------------------------------
serial_putc32:
    push eax
    push edx
    mov ah, al
.wait_tx:
    mov dx, 0x3FD
    in al, dx
    test al, 0x20
    jz .wait_tx
    mov dx, 0x3F8
    mov al, ah
    out dx, al
    pop edx
    pop eax
    ret

puts32:
    push eax
    push esi
.next:
    lodsb
    test al, al
    jz .done
    call serial_putc32
    jmp .next
.done:
    pop esi
    pop eax
    ret

put_dec32:
    push eax
    push ebx
    push ecx
    push edx
    mov ebx, 10
    xor ecx, ecx
.divide:
    xor edx, edx
    div ebx
    push edx
    inc ecx
    test eax, eax
    jnz .divide
.emit:
    pop eax
    add al, '0'
    call serial_putc32
    loop .emit
    pop edx
    pop ecx
    pop ebx
    pop eax
    ret

; --- data --------------------------------------------------------------
align 8
gdt_start:
    dq 0x0000000000000000
gdt_code:
    dq 0x00CF9A000000FFFF
gdt_data:
    dq 0x00CF92000000FFFF
gdt_end:

gdt_descriptor:
    dw gdt_end - gdt_start - 1
    dd gdt_start

idtr:
    dw 0
    dd 0

idt:
    times 256 * 8 db 0

mem_kb:      dw 0
e820_count:  dw 0
tick_count:  dd 0
test_done:   dd 0

msg_stage2:   db "stage2: entered", 13, 10, 0
msg_gdt:      db "gdt: loaded", 13, 10, 0
msg_pmode:    db "pmode: entered", 13, 10, 0
msg_idt:      db "idt: ready", 13, 10, 0
msg_pic:      db "pic: remapped", 13, 10, 0
msg_pit:      db "pit: started", 13, 10, 0
msg_boot:     db "AOS KERNEL ONLINE", 13, 10, 0
msg_mem:      db "memory: ", 0
msg_kb:       db " KB conventional", 13, 10, 0
msg_mode:     db "protected mode: 32-bit", 13, 10, 0
msg_e820:     db "e820: ", 0
msg_entries:  db " entries, ", 0
msg_mb:       db " MB usable", 13, 10, 0
msg_paging:   db "paging: enabled", 13, 10, 0
msg_pageok:   db "paging: remap verified", 13, 10, 0
msg_pagefail: db "paging: REMAP FAILED", 13, 10, 0
msg_fault:    db "fault test: raising #UD", 13, 10, 0
msg_exc:      db "exception 0x", 0
msg_eip:      db " eip=0x", 0
msg_err:      db " err=0x", 0
msg_handled:  db " handled", 13, 10, 0
msg_tick:     db "tick ", 0
msg_crlf:     db 13, 10, 0
msg_done:     db "AOS TICK TEST COMPLETE", 13, 10, 0

; Pad the kernel to the number of sectors stage 1 reads, then pad the whole
; file out to a 1.44 MB floppy image.
times KERNEL_SIZE - ($ - $$) db 0

section .pad
times IMAGE_SIZE - KERNEL_SIZE - 512 db 0
