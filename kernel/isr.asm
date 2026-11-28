; ANVIL - interrupt stubs (32-bit).
; Exceptions 0..31 and IRQ0 hand control to C. The error-code rule matters:
; vectors 8, 10-14, 17 and 21 push a code, the rest do not, so the no-error
; stubs push a dummy to keep one stack layout.
[BITS 32]

extern exception_panic
extern irq_handler

%macro ISR_NOERR 1
global isr%1
isr%1:
    push dword 0
    push dword %1
    jmp isr_common
%endmacro

%macro ISR_ERR 1
global isr%1
isr%1:
    push dword %1
    jmp isr_common
%endmacro

section .text
isr_common:
    pushad
    mov eax, [esp + 32]            ; vector
    mov ebx, [esp + 36]            ; error code
    mov ecx, [esp + 40]            ; eip
    push ecx
    push ebx
    push eax
    call exception_panic
    add esp, 12
    popad
    add esp, 8
    iretd

; Every IRQ vector gets a gate. Installing only IRQ0 was a bet that nothing
; else would ever fire - and real hardware collects on that bet: the 8259 raises
; a spurious IRQ7 during initialisation, delivery hits an empty gate, and the
; machine takes a #GP before the first tick. QEMU never does it.
%macro IRQ_STUB 1
global isr_irq%1
isr_irq%1:
    pushad
    push dword (32 + %1)
    call irq_handler
    add esp, 4
    popad
    iretd
%endmacro

IRQ_STUB 0
IRQ_STUB 1
IRQ_STUB 2
IRQ_STUB 3
IRQ_STUB 4
IRQ_STUB 5
IRQ_STUB 6
IRQ_STUB 7
IRQ_STUB 8
IRQ_STUB 9
IRQ_STUB 10
IRQ_STUB 11
IRQ_STUB 12
IRQ_STUB 13
IRQ_STUB 14
IRQ_STUB 15

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

section .data
global isr_table
isr_table:
    dd isr0,  isr1,  isr2,  isr3,  isr4,  isr5,  isr6,  isr7
    dd isr8,  isr9,  isr10, isr11, isr12, isr13, isr14, isr15
    dd isr16, isr17, isr18, isr19, isr20, isr21, isr22, isr23
    dd isr24, isr25, isr26, isr27, isr28, isr29, isr30, isr31

global irq_table
irq_table:
    dd isr_irq0,  isr_irq1,  isr_irq2,  isr_irq3
    dd isr_irq4,  isr_irq5,  isr_irq6,  isr_irq7
    dd isr_irq8,  isr_irq9,  isr_irq10, isr_irq11
    dd isr_irq12, isr_irq13, isr_irq14, isr_irq15
