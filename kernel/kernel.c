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

#define COM1      0x3F8
#define EXIT_PORT 0x501
#define PAGE_DIR  0x100000

extern u32  isr_table[32];
extern void isr_irq0(void);
extern void exception_panic(u32 vector, u32 error, u32 eip);
extern void timer_tick(void);
extern int virtio_test(void);
extern int store_selftest(void);

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

static void serial_putc(char c)
{
    while ((inb(COM1 + 5) & 0x20) == 0) { }
    outb(COM1, (u8)c);
}

void print(const char *s)
{
    while (*s) serial_putc(*s++);
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
static void heap_selftest(void)
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
        return;
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
}

/* --- entry point ------------------------------------------------------- */
extern u8 __bss_start[];
extern u8 __bss_end[];

void kmain(u32 boot_info_addr)
{
    const boot_info *bi = (const boot_info *)boot_info_addr;
    u8 *cursor;

    /* A flat binary carries no zero-filled segment: clear .bss explicitly so
     * ticks, the IDT and tests_done all start from a known state. */
    for (cursor = __bss_start; cursor < __bss_end; cursor++) *cursor = 0;

    serial_init();
    print("ANVIL KERNEL ONLINE\r\n");
    print("anvil: started\r\n");
    print("anvil: serial online\r\n");

    print("memory: ");
    put_dec(bi->mem_kb);
    print(" KB conventional\r\n");
    print("protected mode: 32-bit\r\n");

    idt_install();
    print("idt: ready\r\n");

    pic_remap();
    print("pic: remapped\r\n");

    pit_start();
    print("pit: started\r\n");

    report_e820(bi);
    enable_paging();

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
            heap_selftest();
        } else {
            print("heap: no usable arena\r\n");
        }
    }

    virtio_test();               /* read sector 0 and hash it */
    store_selftest();            /* M10: records on the bare disk, no filesystem */

    __asm__ __volatile__("sti");

    while (!tests_done) {
        cpu_hlt();
    }

    shell();

    print("fault test: raising #UD\r\n");
    __asm__ __volatile__("ud2");

    for (;;) {
        cpu_cli();
        cpu_hlt();
    }
}
