/* ANVIL - virtio-blk, legacy PCI transport.
 *
 * Round-trip test:
 *   1. read sector 0 and hash it            (host has the same bytes)
 *   2. write a known pattern to sector 1
 *   3. read sector 1 back and compare
 *
 * Legacy (transitional) virtio is deliberate: a fixed I/O BAR layout with no
 * PCI capability walk. Four things about legacy mode cost a debugging cycle
 * each, and are marked below where they matter:
 *
 *   - QueueNum is READ-ONLY; size the rings from the device's value (256).
 *   - Device config is at BAR0 + 0x14, not BAR1 (BAR1 is the modern region).
 *   - The identity map must actually cover the BAR addresses (4 GB, 1024
 *     entries of 4 MB - four entries only cover 16 MB).
 *   - Anything the device writes (used ring, status byte) must be `volatile`
 *     or -O2 caches it in a register and the poll never completes.
 *
 * Memory used (identity-mapped physical RAM above 1 MB):
 *   0x00120000  16 KB ring area: descriptors, available ring, used ring
 *   0x00130000  request header (16 bytes)
 *   0x00131000  read buffer (one sector)
 *   0x00132000  status byte
 *   0x00133000  write buffer (one sector)
 */

typedef unsigned char      u8;
typedef unsigned short     u16;
typedef unsigned int       u32;
typedef unsigned long long u64;

#include "evt.h"          /* M20: machine-readable event records */

extern void print(const char *s);
extern void put_dec(u32 value);
extern void put_hex(u32 value);

#define VQ_BASE     0x00120000u
#define VQ_SPAN     0x00004000u        /* 16 KB, enough for 256 entries */
#define REQ_PHYS    0x00130000u
#define DATA_PHYS   0x00131000u
#define STAT_PHYS   0x00132000u
#define WRBUF_PHYS  0x00133000u

#define QSIZE_MAX   256

/* legacy virtio-pci register offsets from BAR0 (I/O space) */
#define REG_FEATURES      0x00
#define REG_QUEUE_ADDR    0x08
#define REG_QUEUE_NUM     0x0C
#define REG_QUEUE_SEL     0x0E
#define REG_QUEUE_NOTIFY  0x10
#define REG_STATUS        0x12
#define REG_CONFIG        0x14         /* device-specific config starts here */

#define VIRTIO_STATUS_ACK       1
#define VIRTIO_STATUS_DRIVER    2
#define VIRTIO_STATUS_DRIVER_OK 4

#define VIRTQ_DESC_F_NEXT  1
#define VIRTQ_DESC_F_WRITE 2

#define VIRTIO_BLK_T_IN  0
#define VIRTIO_BLK_T_OUT 1

struct vring_desc {
    u64 addr;
    u32 len;
    u16 flags;
    u16 next;
};

struct vring_avail {
    u16 flags;
    u16 idx;
    u16 ring[QSIZE_MAX];
};

struct vring_used_elem {
    u32 id;
    u32 len;
};

struct vring_used {
    u16 flags;
    u16 idx;
    struct vring_used_elem ring[QSIZE_MAX];
};

struct blk_request {
    u32 type;
    u32 reserved;
    u64 sector;
};

struct vring_state {
    u16 io_base;
    u32 desc_base;
    u32 avail_base;
    u32 used_base;
    u16 qsize;
    u16 last_used;
};

/* The boot-time test leaves the device usable; these keep that state so other
 * modules can issue their own requests instead of re-claiming the device. */
static struct vring_state g_vring;
static u64 g_capacity;        /* sectors; 64-bit, because disks exceed 2 TiB */
static int g_ready;

/* --- port I/O ---------------------------------------------------------- */
static inline void outl(u16 port, u32 value)
{
    __asm__ __volatile__("outl %0, %1" : : "a"(value), "Nd"(port));
}

static inline u32 inl(u16 port)
{
    u32 value;
    __asm__ __volatile__("inl %1, %0" : "=a"(value) : "Nd"(port));
    return value;
}

static inline void outw(u16 port, u16 value)
{
    __asm__ __volatile__("outw %0, %1" : : "a"(value), "Nd"(port));
}

static inline u16 inw(u16 port)
{
    u16 value;
    __asm__ __volatile__("inw %1, %0" : "=a"(value) : "Nd"(port));
    return value;
}

static inline void outb_v(u16 port, u8 value)
{
    __asm__ __volatile__("outb %0, %1" : : "a"(value), "Nd"(port));
}

/* --- PCI config space -------------------------------------------------- */
static u32 pci_read32(u8 bus, u8 slot, u8 func, u8 offset)
{
    u32 address = 0x80000000u | ((u32)bus << 16) | ((u32)slot << 11)
                | ((u32)func << 8) | (offset & 0xFCu);
    outl(0xCF8, address);
    return inl(0xCFC);
}

static void pci_write32(u8 bus, u8 slot, u8 func, u8 offset, u32 value)
{
    u32 address = 0x80000000u | ((u32)bus << 16) | ((u32)slot << 11)
                | ((u32)func << 8) | (offset & 0xFCu);
    outl(0xCF8, address);
    outl(0xCFC, value);
}

/* --- hashing ----------------------------------------------------------- */
static u32 fnv1a(const u8 *data, u32 length)
{
    u32 hash = 2166136261u;
    u32 i;

    for (i = 0; i < length; i++) {
        hash ^= data[i];
        hash *= 16777619u;
    }
    return hash;
}

/* --- one block request -------------------------------------------------
 * Returns 0 on success. Reuses descriptors 0..2 for every request, which is
 * safe because each request completes before the next one is built.
 */
static int blk_request(struct vring_state *st, u32 type, u64 sector, u32 data_phys, u32 bytes)
{
    struct blk_request *req = (struct blk_request *)REQ_PHYS;
    volatile u8 *status_byte = (volatile u8 *)STAT_PHYS;
    volatile struct vring_desc *desc = (volatile struct vring_desc *)st->desc_base;
    volatile struct vring_avail *avail = (volatile struct vring_avail *)st->avail_base;
    volatile struct vring_used *used = (volatile struct vring_used *)st->used_base;
    u32 spin;

    req->type = type;
    req->reserved = 0;
    req->sector = sector;

    desc[0].addr = REQ_PHYS;
    desc[0].len = sizeof(struct blk_request);
    desc[0].flags = VIRTQ_DESC_F_NEXT;
    desc[0].next = 1;

    desc[1].addr = data_phys;
    desc[1].len = (u32)bytes;
    desc[1].flags = VIRTQ_DESC_F_NEXT;
    if (type == VIRTIO_BLK_T_IN) {
        desc[1].flags |= VIRTQ_DESC_F_WRITE;   /* device -> memory */
    }
    desc[1].next = 2;

    desc[2].addr = STAT_PHYS;
    desc[2].len = 1;
    desc[2].flags = VIRTQ_DESC_F_WRITE;
    desc[2].next = 0;

    avail->ring[avail->idx % st->qsize] = 0;   /* head of the chain */
    __asm__ __volatile__("" ::: "memory");
    avail->idx = (u16)(avail->idx + 1u);
    __asm__ __volatile__("" ::: "memory");

    outw(st->io_base + REG_QUEUE_NOTIFY, 0);

    for (spin = 0; spin < 200000000u; spin++) {
        if (used->idx != st->last_used) {
            break;
        }
    }
    if (used->idx == st->last_used) {
        print("virtio: TIMEOUT waiting for used ring\n");
        return -1;
    }
    st->last_used = used->idx;

    if (*status_byte != 0) {
        print("virtio: device status ");
        put_dec(*status_byte);
        print("\n");
        return -1;
    }
    return 0;
}

/* --- public read helper ------------------------------------------------
 * One sector from an arbitrary LBA. Thin on purpose: everything difficult
 * already lives in blk_request.
 */
static int blk_read(struct vring_state *st, u64 lba, u32 buffer)
{
    return blk_request(st, VIRTIO_BLK_T_IN, lba, buffer, 512);
}

/* --- the test ---------------------------------------------------------- */
int virtio_test(void)
{
    u8 bus, slot, func;
    u16 io_base = 0;
    u16 io_command = 0;
    u32 vendor_device = 0;
    u32 bar0, bar1;
    u32 desc_base, avail_base, used_base;
    u16 qsize;
    u32 end;

    print("virtio: probing pci\n");

    for (bus = 0; bus < 1; bus++) {
        for (slot = 0; slot < 32; slot++) {
            for (func = 0; func < 8; func++) {
                vendor_device = pci_read32(bus, slot, func, 0x00);
                if ((vendor_device & 0xFFFF) == 0x1AF4) {
                    u32 device = vendor_device >> 16;
                    if (device == 0x1001 || device == 0x1042) {
                        goto found;
                    }
                }
            }
        }
    }

    print("virtio: no device\n");
    return -1;

found:
    print("virtio: found at ");
    put_dec(bus);
    print(":");
    put_dec(slot);
    print(".");
    put_dec(func);
    print(" id=0x");
    put_hex(vendor_device >> 16);
    print("\n");

    io_command = (u16)(pci_read32(bus, slot, func, 0x04) & 0xFFFF);
    pci_write32(bus, slot, func, 0x04, (u32)(io_command | 0x0005));  /* I/O + bus master */

    bar0 = pci_read32(bus, slot, func, 0x10);
    bar1 = pci_read32(bus, slot, func, 0x14);

    if (!(bar0 & 1)) {
        print("virtio: bar0 is not i/o space\n");
        return -1;
    }
    io_base = (u16)(bar0 & 0xFFFCu);

    print("virtio: io=0x");
    put_hex(io_base);
    print(" cfg=0x");
    put_hex(bar1 & 0xFFFFFFF0u);
    print("\n");

    /* legacy device config: BAR0 + 0x14, 64-bit sector count at offset 0 */
    {
        u32 cap_lo = inl((u16)(io_base + REG_CONFIG));
        u32 cap_hi = inl((u16)(io_base + REG_CONFIG + 4));

        /* Both halves, and a 64-bit value kept. Storing only the low dword
         * made a 3 TiB disk look like ~1 TiB and refused every LBA past that
         * boundary as out of range - silently, with every test still passing. */
        print("virtio: capacity lo=");
        put_dec(cap_lo);
        print(" hi=");
        put_dec(cap_hi);
        print("\n");

        g_capacity = ((u64)cap_hi << 32) | (u64)cap_lo;
    }

    /* reset, acknowledge, then claim the device */
    outb_v(io_base + REG_STATUS, 0);
    outb_v(io_base + REG_STATUS, VIRTIO_STATUS_ACK);
    outb_v(io_base + REG_STATUS, VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER);

    outl(io_base + REG_FEATURES, 0);           /* accept no optional features */

    outw(io_base + REG_QUEUE_SEL, 0);
    qsize = inw(io_base + REG_QUEUE_NUM);      /* read-only: use what we are given */
    if (qsize == 0 || qsize > QSIZE_MAX) {
        print("virtio: unusable queue size ");
        put_dec(qsize);
        print("\n");
        return -1;
    }

    desc_base = VQ_BASE;
    avail_base = desc_base + (u32)qsize * 16u;
    used_base = (avail_base + 6u + (u32)qsize * 2u + 0xFFFu) & ~0xFFFu;
    end = used_base + 6u + (u32)qsize * 8u;

    print("virtio: qsize=");
    put_dec(qsize);
    print(" desc=0x");
    put_hex(desc_base);
    print(" used=0x");
    put_hex(used_base);
    print("\n");

    if (end > VQ_BASE + VQ_SPAN) {
        print("virtio: ring area too small\n");
        return -1;
    }

    outl(io_base + REG_QUEUE_ADDR, desc_base >> 12);

    outb_v(io_base + REG_STATUS,
           VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER | VIRTIO_STATUS_DRIVER_OK);

    /* clear ring and buffers */
    {
        u32 *words = (u32 *)VQ_BASE;
        u8 *read_buf = (u8 *)DATA_PHYS;
        u8 *write_buf = (u8 *)WRBUF_PHYS;
        u32 i;

        for (i = 0; i < VQ_SPAN / 4u; i++) words[i] = 0;
        for (i = 0; i < 512; i++) read_buf[i] = 0;
        for (i = 0; i < 512; i++) write_buf[i] = (u8)(i * 7u + 3u);
    }

    {
        struct vring_state *st = &g_vring;
        u8 *read_buf = (u8 *)DATA_PHYS;
        u32 i;
        int same = 1;

        st->io_base = io_base;
        st->desc_base = desc_base;
        st->avail_base = avail_base;
        st->used_base = used_base;
        st->qsize = qsize;
        st->last_used = 0;
        g_ready = 1;

        /* 1. read sector 0 */
        if (blk_request(st, VIRTIO_BLK_T_IN, 0, DATA_PHYS, 512) != 0) {
            return -1;
        }
        evt_begin("virtio.read");
        evt_u32("lba", 0);
        evt_hex32("hash", fnv1a(read_buf, 512));
        evt_end();
        print("virtio: sector0 fnv=0x");
        put_hex(fnv1a(read_buf, 512));
        print("\n");

        /* 2. write the pattern to sector 1 */
        if (blk_request(st, VIRTIO_BLK_T_OUT, 1, WRBUF_PHYS, 512) != 0) {
            return -1;
        }
        print("virtio: sector1 written\n");

        /* 3. read it back over a cleared buffer */
        for (i = 0; i < 512; i++) read_buf[i] = 0;
        if (blk_request(st, VIRTIO_BLK_T_IN, 1, DATA_PHYS, 512) != 0) {
            return -1;
        }

        for (i = 0; i < 512; i++) {
            if (read_buf[i] != (u8)(i * 7u + 3u)) {
                same = 0;
            }
        }

        evt_begin("virtio.write");
        evt_u32("lba", 1);
        evt_hex32("hash", fnv1a(read_buf, 512));
        evt_end();
        print("virtio: writeback fnv=0x");
        put_hex(fnv1a(read_buf, 512));
        print(same ? " roundtrip ok\n" : " ROUNDTRIP MISMATCH\n");

        if (!same) {
            return -1;
        }

        /* arbitrary LBA: sector 2048 carries a marker the host also knows,
         * so reading the wrong sector cannot look like success */
        for (i = 0; i < 512; i++) read_buf[i] = 0;
        if (blk_read(st, 2048u, DATA_PHYS) != 0) {
            return -1;
        }
        print("virtio: sector2048 fnv=0x");
        put_hex(fnv1a(read_buf, 512));
        print("\n");
    }

    return 0;
}

/* --- public block API ---------------------------------------------------
 * One sector at a time. Buffers must be identity-mapped physical memory,
 * which everything in .bss and the heap is. Kept deliberately thin: all the
 * difficulty lives in blk_request above.
 */
int blk_present(void)
{
    return g_ready;
}

u64 blk_capacity_sectors(void)
{
    return g_capacity;
}

int blk_read_sector(u64 lba, void *buffer)
{
    if (!g_ready) return -1;
    if (lba >= g_capacity) return -1;
    return blk_request(&g_vring, VIRTIO_BLK_T_IN, lba, (u32)buffer, 512);
}

int blk_write_sector(u64 lba, const void *buffer)
{
    if (!g_ready) return -1;
    if (lba >= g_capacity) return -1;
    return blk_request(&g_vring, VIRTIO_BLK_T_OUT, lba, (u32)buffer, 512);
}
/* Several consecutive sectors in one request, so that a caller can trade
 * request count against request size. Bounded to keep one request inside a
 * single 64 KB window. */
int blk_write_run(u64 lba, const void *buffer, u32 sectors)
{
    if (!g_ready) return -1;
    if (sectors == 0 || sectors > 128) return -1;
    if (lba + (u64)sectors > g_capacity) return -1;
    return blk_request(&g_vring, VIRTIO_BLK_T_OUT, lba, (u32)buffer, sectors * 512u);
}
