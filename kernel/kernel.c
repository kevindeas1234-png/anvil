/* ANVIL - milestone 4b: the asm kernel's capabilities, in C.
 *
 * Named ANVIL because a kernel is the surface everything else is forged on.
 * AOS (the sandbox, chambers and audit log) is the system that will later run
 * on top of this - different layers, not rivals.
 *
 * Ported from the milestone-3 assembly kernel:
 *   E820 memory map -> IDT with 32 exception gates -> PIC remap -> PIT 100 Hz
 *   -> paging with 4 MB pages -> tick test -> deliberate #UD
 *
 * Freestanding: no libc, no headers, no runtime.
 */

typedef unsigned char      u8;
typedef unsigned short     u16;
typedef unsigned int       u32;
typedef unsigned long long u64;

#include "evt.h"          /* M20: machine-readable event records */

#define COM1      0x3F8
#define EXIT_PORT 0x501
#define PAGE_DIR  0x100000

extern u32  isr_table[32];
extern void isr_irq0(void);
extern void exception_panic(u32 vector, u32 error, u32 eip);
extern void timer_tick(void);
extern int virtio_test(void);
extern int store_selftest(void);
extern u32 store_fault_mode(void);

typedef struct {
    u32 count;      /* number of E820 entries            */
    u32 addr;       /* physical address of the entries   */
    u32 mem_kb;     /* conventional memory from int 12h  */
} boot_info;

typedef struct {
    u64 base;
    u64 length;
    u32 type;
    u32 acpi;
} e820_entry;

static u8 idt[256 * 8] __attribute__((aligned(16)));

struct idtr {
    u16 limit;
    u32 base;
} __attribute__((packed));

static struct idtr idtr;
static volatile u32 ticks;
static volatile int tests_done;
static u32 e820_count;
static u32 e820_mb;
static int shell_exit;
static u32 heap_arena;          /* set by heap_init, reported by the shell */
static u32 heap_size_bytes;

/* --- port I/O and CPU helpers ----------------------------------------- */
static inline void outb(u16 port, u8 value)
{
    __asm__ __volatile__("outb %0, %1" : : "a"(value), "Nd"(port));
}

static inline u8 inb(u16 port)
{
    u8 value;
    __asm__ __volatile__("inb %1, %0" : "=a"(value) : "Nd"(port));
    return value;
}

static inline void cpu_hlt(void) { __asm__ __volatile__("hlt"); }
static inline void cpu_cli(void) { __asm__ __volatile__("cli"); }

/* --- serial ------------------------------------------------------------ */
static void serial_init(void)
{
    outb(COM1 + 1, 0x00);
    outb(COM1 + 3, 0x80);
    outb(COM1 + 0, 0x01);      /* 115200 baud */
    outb(COM1 + 1, 0x00);
    outb(COM1 + 3, 0x03);      /* 8N1 */
    outb(COM1 + 2, 0xC7);
}

/* clang may emit calls to these even with -ffreestanding; provide them so
 * the link never fails on an undefined symbol. */
void *memset(void *dest, int value, u32 count)
{
    u8 *p = (u8 *)dest;
    while (count--) *p++ = (u8)value;
    return dest;
}

void *memcpy(void *dest, const void *src, u32 count)
{
    u8 *d = (u8 *)dest;
    const u8 *s = (const u8 *)src;
    while (count--) *d++ = *s++;
    return dest;
}

void print(const char *s);      /* declared ahead of the ring buffer that uses it */

/* --- recent-output ring -------------------------------------------------
 * A panic that says only "vector 13" is nearly useless. This keeps the last
 * few lines the kernel printed so the fault can be printed with its context,
 * which is usually the only clue that survives.
 */
#define PANIC_LINES 12
#define PANIC_WIDTH 60

static char panic_lines[PANIC_LINES][PANIC_WIDTH];
static int  panic_next;
static int  panic_col;
static int  panic_wrapped;
static int  panic_quiet;      /* set while reporting, so the report cannot capture itself */

static void panic_capture(char c)
{
    if (panic_quiet) return;
    if (c == '\r') return;
    if (c == '\n') {
        panic_lines[panic_next][panic_col] = 0;
        panic_next = (panic_next + 1) % PANIC_LINES;
        if (panic_next == 0) panic_wrapped = 1;
        panic_col = 0;
        return;
    }
    if (panic_col < PANIC_WIDTH - 1) {
        panic_lines[panic_next][panic_col++] = c;
    }
}

static void panic_dump_recent(void)
{
    int count = panic_wrapped ? PANIC_LINES : panic_next;
    int i;

    /* Reading the ring while printing into it corrupts both: the report
     * captured itself and the lines came out interleaved. Stop capturing
     * first, then report. */
    panic_quiet = 1;
    print("panic: recent output\r\n");
    for (i = 0; i < count; i++) {
        int index = (panic_next - count + i + PANIC_LINES * 2) % PANIC_LINES;
        print("  | ");
        print(panic_lines[index]);
        print("\r\n");
    }
}

static const char *vector_name(u32 vector)
{
    switch (vector) {
    case 0:  return "divide error";
    case 6:  return "invalid opcode";
    case 8:  return "double fault";
    case 13: return "general protection";
    case 14: return "page fault";
    default: return "exception";
    }
}

/* --- VGA text console ---------------------------------------------------
 * The serial log is the test artefact; this is for the humans watching the VM.
 * Same characters, same order, written to the 80x25 text buffer at 0xB8000
 * (the identity map covers it). Nothing here feeds back into the serial path,
 * so what verify.py checks stays byte-for-byte unchanged.
 */
#define VGA_MEM   ((volatile u16 *)0xB8000)
#define VGA_COLS  80
#define VGA_ROWS  25
#define VGA_ATTR  0x0F          /* white on black */

/* The top three rows are a header, drawn once and never scrolled. The console
 * owns rows 3..24. Without a reserved region the banner scrolls away in the
 * first second and the machine loses its face. */
#define HEADER_ROWS 3

static u32 vga_row = HEADER_ROWS;
static u32 vga_col;

/* Direct writer for the header: never touches serial, so the log and every hash
 * stay exactly as they were. */
static void vga_text_at(u32 row, u32 col, const char *s)
{
    u32 i = row * VGA_COLS + col;

    while (*s && i < (row + 1) * VGA_COLS) {
        VGA_MEM[i++] = (u16)((VGA_ATTR << 8) | (u8)*s++);
    }
}

static u32  vga_last_step;
static void vga_step(u32 step);   /* defined below; vga_putc pins it on newline */

/* What the machine says about itself, before it says anything else.
 *
 * It says "booting", not "installing": this kernel loads itself and writes
 * nothing to any disk, and rule 2 of the node charter is no lies. The word
 * becomes true the day there is an installer to go with it (M15).
 */
static void vga_banner(void)
{
    vga_text_at(0, 1, "ANVIL - an AOS system");
    vga_text_at(1, 1, "booting from removable disk - nothing is written to it");
    vga_text_at(2, 1, "build 2026-11-29 - every action is recorded");
}

static void vga_scroll(void)
{
    u32 i;

    /* Scroll only the console region: rows above HEADER_ROWS are the header. */
    for (i = HEADER_ROWS * VGA_COLS; i < (VGA_ROWS - 1) * VGA_COLS; i++)
        VGA_MEM[i] = VGA_MEM[i + VGA_COLS];
    for (i = (VGA_ROWS - 1) * VGA_COLS; i < VGA_ROWS * VGA_COLS; i++)
        VGA_MEM[i] = (u16)((VGA_ATTR << 8) | ' ');
}

/* noinline matters here: serial_putc is called from every print/put_dec/put_hex
 * call site in kmain and shell. Once it grew to include the VGA path the
 * compiler inlined ~600 bytes of it into hundreds of sites and .text jumped
 * from 24 KB to 98 KB. Keeping the console writers out of line costs a call
 * per character and keeps the image inside the 32 KB boot window. */
static void __attribute__((noinline)) vga_putc(char c)
{
    if (c == '\r') {
        vga_col = 0;
        return;
    }
    if (c == '\n') {
        vga_col = 0;
        vga_row++;
    } else if (c == '\b') {
        if (vga_col) vga_col--;
        else if (vga_row) { vga_row--; vga_col = VGA_COLS - 1; }
    } else {
        VGA_MEM[vga_row * VGA_COLS + vga_col] = (u16)((VGA_ATTR << 8) | (u8)c);
        if (++vga_col >= VGA_COLS) { vga_col = 0; vga_row++; }
    }
    while (vga_row >= VGA_ROWS) {
        vga_scroll();
        vga_row--;
    }

    /* Pinned AFTER the scroll: writing it before means the shift erases it,
     * which is exactly what happened the first time. Row 0, col 78, so the
     * number survives a full screen of output. */
    vga_step(vga_last_step);
}

static void vga_init(void)
{
    u32 i;
    vga_row = HEADER_ROWS;
    vga_col = 0;
    for (i = 0; i < VGA_ROWS * VGA_COLS; i++)
        VGA_MEM[i] = (u16)((VGA_ATTR << 8) | ' ');
}

/* On a board with no UART at 0x3F8 the line-status register returns whatever
 * the chipset gives for an unimplemented port. If that is 0x00, the transmit
 * wait below never completes: the kernel spins at the first character, before
 * anything reaches the screen, and a firmware watchdog turns the hang into a
 * boot loop. QEMU always provides a UART, so this can only bite on metal.
 *
 * Three changes: the wait is bounded, a dead port is remembered, and the VGA
 * write happens FIRST so the screen shows how far the kernel got even when the
 * serial line is the thing that is broken.
 */
#define SERIAL_SPIN_LIMIT 200000

/* Zero-initialised on purpose: kmain clears .bss before anything else runs, so a
 * flag that defaults to 1 in .data would be the one thing that survived. */
static int serial_dead;

static void __attribute__((noinline)) serial_putc(char c)
{
    vga_putc(c);
    panic_capture(c);

    if (!serial_dead) {
        u32 spins = 0;

        while ((inb(COM1 + 5) & 0x20) == 0) {
            if (++spins > SERIAL_SPIN_LIMIT) {
                serial_dead = 1;    /* give up once, then stop trying */
                return;
            }
        }
        outb(COM1, (u8)c);
    }
}

void print(const char *s)
{
    while (*s) serial_putc(*s++);
}

/* Step counter, written straight to the VGA buffer at row 0, col 78 - before
 * vga_init, without scrolling, and without touching the serial port. On a
 * machine we cannot see into, the last number visible is the whole diagnosis:
 * the coarse notes say which phase, this says which statement.
 *
 *   01 kmain entered        08 e820 done
 *   02 .bss cleared         09 paging on
 *   03 vga ready            10 heap
 *   04 serial ready         11 virtio probed
 *   05 first print          12 store tested
 *   06 idt ready            13 interrupts on
 *   07 pit started          14 shell
 */
static void vga_step(u32 step)
{
    u32 c = 78;

    vga_last_step = step;
    VGA_MEM[c]     = (u16)((0x0F << 8) | (u8)('0' + (step / 10) % 10));
    VGA_MEM[c + 1] = (u16)((0x0F << 8) | (u8)('0' + step % 10));
}

/* Screen-only marker. Never touches the serial port, so it cannot change what
 * verify.py checks - and on metal it is the only evidence left when a hang or a
 * reset happens before the panic path can report anything. */
static void vga_note(const char *s)
{
    vga_putc('[');
    while (*s) vga_putc(*s++);
    vga_putc(']');
    vga_putc('\r');
    vga_putc('\n');
}

void put_dec(u32 value)
{
    char buffer[11];
    int i = 0;

    if (value == 0) {
        serial_putc('0');
        return;
    }
    while (value > 0) {
        buffer[i++] = (char)('0' + (value % 10));
        value /= 10;
    }
    while (i-- > 0) serial_putc(buffer[i]);
}

void put_hex(u32 value)
{
    int shift;
    for (shift = 28; shift >= 0; shift -= 4) {
        u32 digit = (value >> shift) & 0xF;
        serial_putc(digit < 10 ? (char)('0' + digit) : (char)('a' + digit - 10));
    }
}

/* --- machine-readable events (M20) --------------------------------------
 * The prose above is for humans. These records are for the agent: fields it
 * can assert on, instead of sentences it has to match. The hash covers the
 * line up to " hash=", so a truncated record cannot still verify.
 */
static u32 evt_seq;
static u32 evt_h;

static void __attribute__((noinline)) evt_char(char c)
{
    serial_putc(c);
    evt_h = (evt_h ^ (u8)c) * 16777619u;
}

static void evt_puts(const char *s)
{
    while (*s) evt_char(*s++);
}

static void evt_dec(u32 v)
{
    char buf[11];
    int i = 0;

    if (!v) { evt_char('0'); return; }
    while (v) { buf[i++] = (char)('0' + (v % 10)); v /= 10; }
    while (i-- > 0) evt_char(buf[i]);
}

static void evt_hex(u32 v)
{
    int shift;

    evt_puts("0x");
    for (shift = 28; shift >= 0; shift -= 4) {
        u32 digit = (v >> shift) & 0xF;
        evt_char(digit < 10 ? (char)('0' + digit) : (char)('a' + digit - 10));
    }
}

void evt_begin(const char *id)
{
    evt_h = 2166136261u;            /* FNV-1a offset basis */
    evt_seq++;
    evt_puts("evt id=");
    evt_puts(id);
    evt_puts(" seq=");
    evt_dec(evt_seq);
}

void evt_u32(const char *key, unsigned value)
{
    evt_char(' ');
    evt_puts(key);
    evt_char('=');
    evt_dec(value);
}

void evt_hex32(const char *key, unsigned value)
{
    evt_char(' ');
    evt_puts(key);
    evt_char('=');
    evt_hex(value);
}

void evt_str(const char *key, const char *value)
{
    evt_char(' ');
    evt_puts(key);
    evt_char('=');
    evt_puts(value);
}

void evt_end(void)
{
    u32 v = evt_h;
    int i;

    /* chk=, not hash=: several events carry a field called `hash` (the payload
     * hash), and one line cannot have two fields with the same key. Found the
     * moment the first virtio record printed two of them.
     *
     * Written with serial_putc, not evt_char: the checksum is not part of what
     * the checksum covers. */
    serial_putc(' '); serial_putc('c'); serial_putc('h'); serial_putc('k');
    serial_putc('='); serial_putc('0'); serial_putc('x');
    for (i = 7; i >= 0; i--) {
        u32 digit = (v >> (i * 4)) & 0xF;
        serial_putc(digit < 10 ? (char)('0' + digit) : (char)('a' + digit - 10));
    }
    serial_putc('\r');
    serial_putc('\n');
}

/* --- IDT --------------------------------------------------------------- */
static void idt_set(int vector, u32 handler)
{
    u8 *entry = idt + vector * 8;

    entry[0] = (u8)(handler & 0xFF);
    entry[1] = (u8)((handler >> 8) & 0xFF);
    entry[2] = 0x08;                 /* kernel code selector */
    entry[3] = 0x00;
    entry[4] = 0x00;
    entry[5] = 0x8E;                 /* present, ring 0, 32-bit interrupt gate */
    entry[6] = (u8)((handler >> 16) & 0xFF);
    entry[7] = (u8)((handler >> 24) & 0xFF);
}

static void idt_install(void)
{
    int i;

    for (i = 0; i < 32; i++) idt_set(i, isr_table[i]);
    idt_set(0x20, (u32)isr_irq0);

    idtr.limit = (u16)(sizeof(idt) - 1);
    idtr.base = (u32)idt;
    __asm__ __volatile__("lidt %0" : : "m"(idtr));
}

/* --- 8259 PIC ---------------------------------------------------------- */
static void pic_remap(void)
{
    outb(0x20, 0x11); outb(0xA0, 0x11);
    outb(0x21, 0x20); outb(0xA1, 0x28);
    outb(0x21, 0x04); outb(0xA1, 0x02);
    outb(0x21, 0x01); outb(0xA1, 0x01);
    outb(0x21, 0xFE);              /* unmask IRQ0 only */
    outb(0xA1, 0xFF);
}

/* --- PIT --------------------------------------------------------------- */
static void pit_start(void)
{
    u16 divisor = 11932;           /* 1193182 / 11932 ~ 100 Hz */

    outb(0x43, 0x36);
    outb(0x40, (u8)(divisor & 0xFF));
    outb(0x40, (u8)(divisor >> 8));
}

/* --- E820 -------------------------------------------------------------- */
static void report_e820(const boot_info *bi)
{
    const e820_entry *entries = (const e820_entry *)bi->addr;
    u32 usable = 0;
    u32 i;

    print("e820: ");
    put_dec(bi->count);
    print(" entries, ");

    for (i = 0; i < bi->count; i++) {
        if (entries[i].type == 1) {
            usable += (u32)entries[i].length;
        }
    }

    e820_count = bi->count;
    e820_mb = usable >> 20;

    put_dec(usable >> 20);
    print(" MB usable\r\n");

    evt_begin("boot.e820");
    evt_u32("entries", bi->count);
    evt_u32("usable_kb", usable >> 10);
    evt_end();
}

/* --- serial console -----------------------------------------------------
 * The link to the outside world: AOS (or any terminal) writes a line, the
 * kernel answers. All polling - no IRQ4 - so it cannot deadlock the timer.
 */
static int serial_rx_ready(void)
{
    return (inb(COM1 + 5) & 0x01) != 0;
}

static int streq(const char *a, const char *b)
{
    while (*a && *a == *b) {
        a++;
        b++;
    }
    return *a == 0 && *b == 0;
}

static void run_command(const char *line)
{
    if (streq(line, "help")) {
        print("commands: help, mem, heap, ticks, exit\r\n");
    } else if (streq(line, "mem")) {
        print("e820 entries: ");
        put_dec(e820_count);
        print(", usable MB: ");
        put_dec(e820_mb);
        print("\r\n");
    } else if (streq(line, "heap")) {
        print("arena 0x");
        put_hex(heap_arena);
        print(", " );
        put_dec(heap_size_bytes >> 10);
        print(" KB\r\n");
    } else if (streq(line, "ticks")) {
        put_dec(ticks);
        print(" ticks since boot\r\n");
    } else if (streq(line, "exit")) {
        shell_exit = 1;
    } else {
        print("unknown command: ");
        print(line);
        print("\r\n");
    }
}

/* Idle-exit: with no link attached (file-backed serial), the loop times out
 * and the self-test continues, so CI still terminates on its own. */
static void shell(void)
{
    char line[64];
    u32 len = 0;
    u32 last_activity;
    int seen_input = 0;

    print("\r\nANVIL shell ready. type 'help'\r\nanvil> ");
    last_activity = ticks;

    while (!shell_exit) {
        char c;

        if (!serial_rx_ready()) {
            /* Short timeout when nobody is talking (keeps CI terminating);
             * generous once a session is under way. */
            u32 limit = seen_input ? 1500 : 400;

            if (ticks - last_activity > limit) {
                print("\r\n");
                return;
            }
            continue;
        }

        c = (char)inb(COM1);
        last_activity = ticks;
        seen_input = 1;

        if (c == '\r' || c == '\n') {
            line[len] = 0;
            print("\r\n");
            if (len) {
                run_command(line);
            }
            len = 0;
            if (!shell_exit) {
                print("anvil> ");
            }
            continue;
        }

        if ((c == 8 || c == 127) && len) {
            len--;
            print("\b \b");
            continue;
        }

        if (len < sizeof(line) - 1) {
            line[len++] = c;
            serial_putc(c);
        }
    }

    print("\r\n");
}

/* --- paging ------------------------------------------------------------ */
static void enable_paging(void)
{
    u32 *pd = (u32 *)PAGE_DIR;
    int i;

    /* All 1024 entries, not four: each 4 MB page covers 4 MB, so four entries
     * map 16 MB. Device BARs live near the top of the 4 GB space and fault
     * without a full identity map. MMIO pages are marked uncached (PCD). */
    for (i = 0; i < 1024; i++) {
        u32 flags = 0x83;
        if (i >= 960) {
            flags |= 0x10;         /* 0xF0000000..0xFFFFFFFF: PCI MMIO */
        }
        pd[i] = ((u32)i << 22) | flags;
    }
    pd[768] = 0x00000083;          /* 0xC0000000 -> physical 0 (remap test) */

    __asm__ __volatile__("mov %%cr4, %%eax\n"
                         "or $0x10, %%eax\n"
                         "mov %%eax, %%cr4" : : : "eax");
    __asm__ __volatile__("mov %0, %%cr3" : : "r"(PAGE_DIR) : "memory");
    __asm__ __volatile__("mov %%cr0, %%eax\n"
                         "or $0x80000000, %%eax\n"
                         "mov %%eax, %%cr0" : : : "eax", "memory");

    print("paging: enabled\r\n");

    *(volatile u32 *)0xC0200000 = 0xA05A05A0;
    if (*(volatile u32 *)0x00200000 == 0xA05A05A0) {
        print("paging: remap verified\r\n");
        evt_begin("page.on");
        evt_u32("verified", 1);
        evt_end();
    } else {
        print("paging: REMAP FAILED\r\n");
    }
}

/* --- interrupt callbacks (called from isr.asm) ------------------------- */
void timer_tick(void)
{
    outb(0x20, 0x20);              /* EOI */

    ticks++;
    if (ticks <= 10) {
        print("tick ");
        put_dec(ticks);
        print("\r\n");
        evt_begin("tick");
        evt_u32("n", ticks);
        evt_end();
    } else if (ticks == 11) {
        print("AOS TICK TEST COMPLETE\r\n");
        tests_done = 1;
    }
}

void exception_panic(u32 vector, u32 error, u32 eip)
{
    print("exception 0x");
    put_hex(vector);
    print(" eip=0x");
    put_hex(eip);
    print(" err=0x");
    put_hex(error);
    print(" handled\r\n");

    /* M13: a fault with no context is a mystery. Say what it was, where it
     * happened, and what the kernel had just been doing. */
    print("panic: vector=0x");
    put_hex(vector);
    print(" ");
    print((char *)vector_name(vector));
    print("\r\n");
    print("panic: step=");
    put_dec(vga_last_step);   /* how far the kernel had got, as a number */
    print("\r\n");
    print("panic: eip=0x");
    put_hex(eip);
    print(" err=0x");
    put_hex(error);
    print("\r\n");
    panic_dump_recent();

    /* After the dump, so the record cannot capture itself into the ring. */
    evt_begin("panic");
    evt_hex32("vector", vector);
    evt_hex32("eip", eip);
    evt_hex32("err", error);
    evt_u32("step", vga_last_step);
    evt_end();

    print("panic: halted\r\n");

    outb(EXIT_PORT, 0x10);

    for (;;) {
        cpu_cli();
        cpu_hlt();
    }
}

/* --- heap ---------------------------------------------------------------
 * First-fit free list over an arena carved out of usable E820 memory.
 * Blocks carry a header; kfree coalesces adjacent free blocks. The arena is
 * identity-mapped physical memory, so no translation games are involved.
 */
typedef struct heap_block {
    u32 size;                      /* payload bytes                  */
    u32 free;                      /* 1 = available                  */
    struct heap_block *next;
} heap_block;

static heap_block *heap_head;   /* arena address/size live with the other
                                   kernel statics, above the shell code */

static void heap_init(u32 base, u32 size)
{
    heap_head = (heap_block *)base;
    heap_head->size = size - (u32)sizeof(heap_block);
    heap_head->free = 1;
    heap_head->next = 0;
    heap_arena = base;
    heap_size_bytes = size;
}

void *kmalloc(u32 size)
{
    heap_block *b = heap_head;

    size = (size + 7) & ~7u;

    while (b) {
        if (b->free && b->size >= size) {
            if (b->size >= size + (u32)sizeof(heap_block) + 16) {
                heap_block *split =
                    (heap_block *)((u8 *)b + sizeof(heap_block) + size);
                split->size = b->size - size - (u32)sizeof(heap_block);
                split->free = 1;
                split->next = b->next;
                b->size = size;
                b->next = split;
            }
            b->free = 0;
            return (u8 *)b + sizeof(heap_block);
        }
        b = b->next;
    }
    return 0;
}

void kfree(void *ptr)
{
    heap_block *b;

    if (!ptr) {
        return;
    }
    ((heap_block *)((u8 *)ptr - sizeof(heap_block)))->free = 1;

    b = heap_head;
    while (b) {
        if (b->free && b->next && b->next->free) {
            b->size += (u32)sizeof(heap_block) + b->next->size;
            b->next = b->next->next;
        }
        b = b->next;
    }
}

/* An arena is only taken if E820 says the range is really usable RAM. */
static u32 find_arena(const boot_info *bi, u32 *size)
{
    const e820_entry *entries = (const e820_entry *)bi->addr;
    u32 base = 0x00200000;         /* 2 MB - above the page directory */
    u32 want = 0x00100000;         /* 1 MB arena                      */
    u32 i;

    for (i = 0; i < bi->count; i++) {
        u64 start = entries[i].base;
        u64 end = start + entries[i].length;

        if (entries[i].type == 1 && start <= base && end >= (u64)base + want) {
            *size = want;
            return base;
        }
    }
    *size = 0;
    return 0;
}

/* Allocate, write, free, reallocate: proves reuse and that live data survives. */
static int heap_selftest(void)
{
    u8 *a;
    u8 *b;
    u8 *d;
    u32 i;
    int ok = 1;

    a = (u8 *)kmalloc(64);
    b = (u8 *)kmalloc(128);
    if (!a || !b) {
        print("heap: alloc/free FAILED\r\n");
        return 0;
    }

    for (i = 0; i < 64; i++) a[i] = (u8)i;
    for (i = 0; i < 128; i++) b[i] = (u8)(i * 3);

    kfree(b);
    d = (u8 *)kmalloc(96);
    if (!d) {
        ok = 0;
    } else {
        for (i = 0; i < 96; i++) d[i] = 0xAA;
    }

    for (i = 0; i < 64; i++) {
        if (a[i] != (u8)i) ok = 0;
    }

    print(ok ? "heap: alloc/free ok\r\n" : "heap: alloc/free FAILED\r\n");
    return ok;
}

/* --- entry point ------------------------------------------------------- */
extern u8 __bss_start[];
extern u8 __bss_end[];

void kmain(u32 boot_info_addr)
{
    const boot_info *bi = (const boot_info *)boot_info_addr;
    u8 *cursor;

    vga_step(1);                 /* kmain entered: the load path and the jump worked */

    /* A flat binary carries no zero-filled segment: clear .bss explicitly so
     * ticks, the IDT and tests_done all start from a known state. */
    for (cursor = __bss_start; cursor < __bss_end; cursor++) *cursor = 0;
    vga_step(2);                 /* .bss cleared: the linker symbols are sane */

    vga_init();
    vga_banner();
    vga_step(3);
    vga_note("vga");          /* proves the screen works before anything else */
    serial_init();
    vga_step(4);
    vga_note("serial");       /* if this appears and 'ANVIL' does not, the UART is the problem */
    print("ANVIL KERNEL ONLINE\r\n");   /* the first serial write is the risky one */
    vga_step(5);
    print("anvil: started\r\n");
    print("anvil: serial online\r\n");

    print("memory: ");
    put_dec(bi->mem_kb);
    print(" KB conventional\r\n");

    evt_begin("boot.mem");
    evt_u32("kb", bi->mem_kb);
    evt_end();
    print("protected mode: 32-bit\r\n");

    idt_install();
    vga_step(6);
    print("idt: ready\r\n");

    pic_remap();
    print("pic: remapped\r\n");

    pit_start();
    vga_step(7);
    print("pit: started\r\n");

    report_e820(bi);
    vga_step(8);
    vga_note("e820");
    enable_paging();
    vga_step(9);
    vga_note("paging on");

    {   /* allocator, carved from real firmware-described memory */
        u32 arena_size = 0;
        u32 arena = find_arena(bi, &arena_size);

        if (arena) {
            heap_init(arena, arena_size);
            print("heap: arena ");
            put_dec(heap_size_bytes >> 10);
            print(" KB at 0x");
            put_hex(heap_arena);
            print("\r\n");
            if (heap_selftest()) {
                evt_begin("heap.ok");
                evt_hex32("arena", heap_arena);
                evt_u32("size_kb", heap_size_bytes >> 10);
                evt_end();
            }
        } else {
            print("heap: no usable arena\r\n");
        }
    }

    vga_step(10);                /* heap done */
    vga_step(11);
    vga_note("virtio");
    virtio_test();               /* read sector 0 and hash it */
    vga_step(12);
    vga_note("store");
    store_selftest();            /* M10: records on the bare disk, no filesystem */

    vga_step(13);
    __asm__ __volatile__("sti");
    vga_step(14);                /* interrupts are on and we survived enabling them */

    vga_step(15);                /* reached the wait loop */
    while (!tests_done) {
        cpu_hlt();
    }
    vga_step(16);                /* ticks finished */

    shell();

    print("fault test: raising #UD\r\n");
    if (store_fault_mode() == 0x0D) {
        /* A genuine protection fault rather than an invalid opcode. A null
         * dereference cannot be used here: the identity map covers all 4 GiB,
         * so address 0 is as valid as any other. Loading a selector that is
         * not in the GDT faults for real. */
        print("fault test: mode=general protection\r\n");
        __asm__ __volatile__("mov $0x28, %ax\n\tmov %ax, %ds");
        print("fault test: no fault raised\r\n");
    } else {
        __asm__ __volatile__("ud2");
    }

    for (;;) {
        cpu_cli();
        cpu_hlt();
    }
}
