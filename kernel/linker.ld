/* ANVIL kernel image layout.
 * Flat binary, loaded by stage 1 at physical 0x10000.
 */
ENTRY(_start)

SECTIONS
{
    . = 0x10000;

    .text : {
        *(.text.entry)      /* _start must be the very first bytes */
        *(.text*)
    }

    .rodata : { *(.rodata*) }
    .data   : { *(.data*) }

    .bss : {
        __bss_start = .;    /* kmain zeroes this range itself */
        *(.bss*) *(COMMON)
        __bss_end = .;
    }

    /DISCARD/ : { *(.eh_frame) *(.comment) *(.note*) }
}
