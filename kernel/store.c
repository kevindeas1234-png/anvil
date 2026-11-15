/* ANVIL - M10: a raw record store, with no partition table and no filesystem.
 *
 * The disk has no opinion about partitions; what a partition table actually
 * provides is a *convention for finding things*. Skipping it means supplying
 * that convention ourselves, and this file is it:
 *
 *   LBA 0        superblock: magic, geometry, and the committed record count
 *   LBA 1..15    reserved, unread
 *   LBA 16..     fixed 4-sector records, record i at 16 + i*4
 *
 * No index is needed because records are fixed size: the count locates
 * everything. Append writes the record first and bumps the count second, so a
 * crash between the two leaves an orphan that the count simply ignores. That
 * is crash consistency without a journal, for the price of one sector rewrite.
 *
 * The on-disk format is defined by tools/rawstore_check.py, which was written
 * first and independently. If the two disagree, one of them is wrong - and
 * finding that out is the point.
 */

typedef unsigned char      u8;
typedef unsigned int       u32;
typedef unsigned long long u64;

extern void print(const char *s);
extern void put_dec(u32 value);
extern void put_hex(u32 value);
extern void *memset(void *dest, int value, u32 count);
extern void *memcpy(void *dest, const void *src, u32 count);

/* virtio.c */
extern int blk_read_sector(u64 lba, void *buffer);
extern int blk_write_sector(u64 lba, const void *buffer);
extern int blk_write_run(u64 lba, const void *buffer, u32 sectors);
extern u64 blk_capacity_sectors(void);
extern int blk_present(void);

#define SECTOR       512u
#define DATA_START   16u
#define REC_SECTORS  4u
#define REC_SIZE     (REC_SECTORS * SECTOR)      /* 2048 */
#define HEADER_BYTES 32u
#define MAX_PAYLOAD  (REC_SIZE - HEADER_BYTES)   /* 2016 */
#define STORE_CAPACITY ((16384u - DATA_START) / REC_SECTORS)   /* 4092 */

static u8  sb[SECTOR]  __attribute__((aligned(16)));
static u8  rec[REC_SIZE] __attribute__((aligned(16)));
static u8  spare[REC_SIZE] __attribute__((aligned(16)));
static u8  pol[SECTOR] __attribute__((aligned(16)));
static u32 g_count;

/* --- policy: data, not code --------------------------------------------
 * The kernel supplies the mechanisms and the counts; how much work to do is a
 * decision that belongs above it, so it is read from a sector rather than
 * compiled in. Values are clamped: a policy may ask for more or less work, it
 * may never ask for a different result.
 */
static u32 g_chunk = 1;        /* sectors per device request, 1..REC_SECTORS */
static u32 g_flush = 1;        /* commit the superblock every N appends */
static u32 g_sectors_moved;    /* every sector written, including commits */
static u32 g_requests;         /* every device request issued */

/* --- little-endian helpers ---------------------------------------------- */
static void put_u32(u8 *p, u32 v)
{
    p[0] = (u8)(v & 0xFF);
    p[1] = (u8)((v >> 8) & 0xFF);
    p[2] = (u8)((v >> 16) & 0xFF);
    p[3] = (u8)((v >> 24) & 0xFF);
}

static u32 get_u32(const u8 *p)
{
    return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24);
}

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

/* memcmp is not in this kernel; comparing a fixed few bytes is all this needs. */
static int memcmp8(const u8 *a, const char *b, u32 n)
{
    u32 i;
    for (i = 0; i < n; i++) {
        if (a[i] != (u8)b[i]) return 1;
    }
    return 0;
}

/* --- geometry ----------------------------------------------------------- */
static u32 lba_of(u32 index)
{
    return DATA_START + index * REC_SECTORS;
}

/* The same generator the host verifier uses. Byte-identical output in both
 * languages is what makes the comparison meaningful. */
static void payload_for(u32 index, u8 *out, u32 length)
{
    u32 state = 0x12345678u + index;
    u32 i;

    for (i = 0; i < length; i++) {
        state = state * 1103515245u + 12345u;
        out[i] = (u8)((state >> 16) & 0xFF);
    }
}

/* --- superblock --------------------------------------------------------- */
static u32 superblock_fnv(void)
{
    return fnv1a(sb, 0x18);
}

static void superblock_build(u32 count)
{
    memset(sb, 0, SECTOR);
    memcpy(sb, "ANVILRS1", 8);
    put_u32(sb + 0x08, 1);                 /* version */
    put_u32(sb + 0x0C, REC_SECTORS);
    put_u32(sb + 0x10, DATA_START);
    put_u32(sb + 0x14, count);
    put_u32(sb + 0x18, superblock_fnv());
}

static int superblock_read(void)
{
    if (blk_read_sector(0, sb) != 0) {
        return -1;
    }
    if (memcmp8(sb, "ANVILRS1", 8) != 0) {
        return -1;
    }
    if (get_u32(sb + 0x08) != 1 || get_u32(sb + 0x0C) != REC_SECTORS
        || get_u32(sb + 0x10) != DATA_START) {
        return -1;
    }
    if (get_u32(sb + 0x18) != superblock_fnv()) {
        return -1;
    }
    g_count = get_u32(sb + 0x14);
    if (g_count > STORE_CAPACITY) {
        return -1;
    }
    return 0;
}

static int commit_count(u32 count)
{
    superblock_build(count);
    if (blk_write_sector(0, sb) != 0) {
        return -1;
    }
    g_sectors_moved += 1;
    g_requests += 1;
    g_count = count;
    return 0;
}

int store_format(void)
{
    u32 sector;

    /* Zero the reserved area as well as writing the superblock. Without this
     * the region hash depends on whatever happened to be on the disk before
     * the format, which makes a formatter that does not fully format.
     * Sector 1 is skipped: it holds the policy, which is configuration and
     * must survive a format. */
    memset(sb, 0, SECTOR);
    for (sector = 1; sector < DATA_START; sector++) {
        if (blk_write_sector(sector, sb) != 0) return -1;
        /* counted, or the metric lies: a format is real work */
        g_sectors_moved += 1;
        g_requests += 1;
    }
    return commit_count(0);
}

/* --- policy table ------------------------------------------------------- */
static void policy_load(void)
{
    if (blk_read_sector(blk_capacity_sectors() - 1u, pol) != 0) {
        print("store: policy absent (defaults)\n");
        return;
    }
    if (memcmp8(pol, "ANVLPOL1", 8) != 0
        || get_u32(pol + 0x10) != fnv1a(pol, 0x10)) {
        print("store: policy invalid (defaults)\n");
        return;
    }

    g_chunk = (u32)pol[0x08] | ((u32)pol[0x09] << 8);
    g_flush = (u32)pol[0x0A] | ((u32)pol[0x0B] << 8);

    if (g_chunk < 1) g_chunk = 1;
    if (g_chunk > REC_SECTORS) g_chunk = REC_SECTORS;
    if (g_flush < 1) g_flush = 1;
    if (g_flush > 64) g_flush = 64;

    print("store: policy chunk=");
    put_dec(g_chunk);
    print(" flush=");
    put_dec(g_flush);
    print("\n");
}

int store_init(void)
{
    if (!blk_present()) return -1;
    policy_load();
    if (superblock_read() == 0) return 0;
    if (store_format() != 0) return -1;
    return 1;                       /* 1 means "was blank, formatted now" */
}

u32 store_count(void)
{
    return g_count;
}

/* --- records ------------------------------------------------------------ */
static void record_build(u8 *out, u32 index, u32 seq, const u8 *payload, u32 length)
{
    memset(out, 0, REC_SIZE);
    memcpy(out, "REC1", 4);
    put_u32(out + 0x04, index);
    put_u32(out + 0x08, length);
    put_u32(out + 0x0C, fnv1a(payload, length));
    put_u32(out + 0x10, seq);
    put_u32(out + 0x14, fnv1a(out, 0x14));
    memcpy(out + HEADER_BYTES, payload, length);
}

/* The chunk size is the one place a policy can trade request count against
 * request size. Fewer, larger requests move the same bytes. */
static int record_write(u32 index)
{
    u32 s = 0;

    while (s < REC_SECTORS) {
        u32 n = g_chunk;

        if (n > REC_SECTORS - s) n = REC_SECTORS - s;
        if (blk_write_run(lba_of(index) + s, rec + s * SECTOR, n) != 0) {
            return -1;
        }
        g_sectors_moved += n;
        g_requests += 1;
        s += n;
    }
    return 0;
}

/* Commit deferral: the superblock rewrite is one sector per commit, so a policy
 * that commits every N appends writes fewer sectors. Correctness is unaffected
 * because the count remains the only commit point - an uncommitted record is an
 * orphan and is ignored on the next boot. */
static u32 g_pending;

int store_flush(void)
{
    if (g_pending == 0) {
        return 0;
    }
    g_pending = 0;
    return commit_count(g_count);
}

static int record_read(u32 index)
{
    u32 s;

    for (s = 0; s < REC_SECTORS; s++) {
        if (blk_read_sector(lba_of(index) + s, rec + s * SECTOR) != 0) {
            return -1;
        }
    }
    return 0;
}

static int record_check(u32 index, u32 *payload_fnv)
{
    u32 length;

    if (record_read(index) != 0) return -1;
    if (memcmp8(rec, "REC1", 4) != 0) return -2;
    if (get_u32(rec + 0x04) != index) return -3;
    length = get_u32(rec + 0x08);
    if (length > MAX_PAYLOAD) return -4;
    if (get_u32(rec + 0x14) != fnv1a(rec, 0x14)) return -5;

    *payload_fnv = fnv1a(rec + HEADER_BYTES, length);
    if (*payload_fnv != get_u32(rec + 0x0C)) return -6;
    return 0;
}

/* Append one record. The payload is generated from the index so that the host
 * can reproduce every byte without being told what was written. */
static u8 payload_buf[MAX_PAYLOAD];

int store_append_generated(void)
{
    u32 index = g_count;
    u8 *payload = payload_buf;

    if (index >= STORE_CAPACITY) return -1;

    payload_for(index, payload, MAX_PAYLOAD);
    record_build(rec, index, index + 1, payload, MAX_PAYLOAD);
    if (record_write(index) != 0) return -1;      /* record first... */
    g_count = index + 1;
    g_pending += 1;
    if (g_pending >= g_flush) {                   /* ...count second, maybe later */
        if (store_flush() != 0) return -1;
    }
    return (int)index;
}

/* Read one committed record's payload. Anything at or past the count is
 * refused: an orphan record on disk is not part of the store. */
int store_read(u32 index, u8 *out, u32 *length)
{
    u32 hash;

    if (index >= g_count) return -1;
    if (record_check(index, &hash) != 0) return -2;
    *length = get_u32(rec + 0x08);
    memcpy(out, rec + HEADER_BYTES, *length);
    return 0;
}


/* Read the device's last sector and report where it was and what it hashed to.
 *
 * This is the check the suite was missing: every other test works near the start
 * of the disk, so a 32-bit sector count could describe a 3 TiB device as ~1 TiB
 * and nothing failed. A read at capacity-1 cannot pass unless the whole 64-bit
 * LBA path works. */
static void store_probe_last_sector(void)
{
    u64 capacity = blk_capacity_sectors();
    u64 lba = capacity - 1u;

    if (blk_read_sector(lba, spare) != 0) {
        print("store: last sector unreadable\n");
        return;
    }
    print("store: last sector lba_lo=");
    put_dec((u32)(lba & 0xFFFFFFFFu));
    print(" lba_hi=");
    put_dec((u32)(lba >> 32));
    print(" fnv=0x");
    put_hex(fnv1a(spare, SECTOR));
    print("\n");
}
/* --- verification ------------------------------------------------------- */
static int store_verify(u32 *first_bad)
{
    u32 i;
    u32 hash;

    for (i = 0; i < g_count; i++) {
        if (record_check(i, &hash) != 0) {
            *first_bad = i;
            return -1;
        }
    }
    *first_bad = 0;
    return 0;
}

/* One number covering the superblock and every committed record. The host
 * computes the same value from the same layout, so it cannot be faked by a
 * kernel that reports its own success. */
static u32 region_fnv(void)
{
    u32 sectors = DATA_START + g_count * REC_SECTORS;
    u32 hash = 2166136261u;
    u32 s;
    u32 i;

    for (s = 0; s < sectors; s++) {
        if (blk_read_sector(s, spare) != 0) return 0;
        for (i = 0; i < SECTOR; i++) {
            hash ^= spare[i];
            hash *= 16777619u;
        }
    }
    return hash;
}

/* --- the self-test ------------------------------------------------------ */
int store_selftest(void)
{
    u32 bad = 0;
    u32 first = 0;
    u32 hash0 = 0, hash63 = 0;
    u32 region_first = 0, region_second = 0;
    u32 saved;
    int i;

    if (!blk_present()) {
        print("store: no block device\n");
        return -1;
    }
    if (store_init() < 0) {
        print("store: init failed\n");
        return -1;
    }

    /* 1. a known state to work from */
    if (store_format() != 0) {
        print("store: format failed\n");
        return -1;
    }
    print("store: formatted ");
    put_dec(STORE_CAPACITY);
    print(" slots\n");

    /* 2. append 64 records */
    for (i = 0; i < 64; i++) {
        if (store_append_generated() < 0) {
            print("store: append failed at ");
            put_dec((u32)i);
            print("\n");
            return -1;
        }
    }
    if (store_flush() != 0) {
        print("store: flush failed\n");
        return -1;
    }
    print("store: appended 64, count=");
    put_dec(g_count);
    print("\n");
    print("store: moved ");
    put_dec(g_sectors_moved);
    print(" sectors, ");
    put_dec(g_requests);
    print(" requests\n");

    /* 3/4. read the first and last back and report their payload hashes */
    record_check(0, &hash0);
    record_check(63, &hash63);
    print("store: rec0 fnv=0x");
    put_hex(hash0);
    print("\n");
    print("store: rec63 fnv=0x");
    put_hex(hash63);
    print("\n");

    /* 5. verify every record */
    if (store_verify(&first) == 0) {
        print("store: verify ok ");
        put_dec(g_count);
        print("/");
        put_dec(g_count);
        print("\n");
    } else {
        print("store: verify FAIL rec ");
        put_dec(first);
        print("\n");
        return -1;
    }

    /* 6. superblock hash, and the hash of the whole committed region */
    print("store: superblock fnv=0x");
    put_hex(superblock_fnv());
    print("\n");
    region_first = region_fnv();
    print("store: region fnv=0x");
    put_hex(region_first);
    print("\n");

    /* 7. an orphan: write record 64 without committing it */
    payload_for(64, spare + HEADER_BYTES, MAX_PAYLOAD);
    record_build(rec, 64, 65, spare + HEADER_BYTES, MAX_PAYLOAD);
    if (record_write(64) != 0) return -1;
    if (superblock_read() != 0 || g_count != 64) {
        print("store: orphan test saw a changed count\n");
        return -1;
    }
    if (record_read(64) == 0 && memcmp8(rec, "REC1", 4) == 0) {
        print("store: orphan ignored\n");
    } else {
        print("store: orphan test wrote nothing\n");
        return -1;
    }

    /* 8. corrupt one byte of a committed record: verification must fail */
    if (record_read(32) != 0) return -1;
    memcpy(spare, rec, REC_SIZE);
    rec[HEADER_BYTES + 5] ^= 0xFF;
    if (record_write(32) != 0) return -1;
    bad = store_verify(&first);
    if (bad == 0) {
        print("store: verify passed a corrupted record\n");
        return -1;
    }
    print("store: verify FAIL rec ");
    put_dec(first);
    print("\n");

    /* restore it and prove the store is clean again */
    memcpy(rec, spare, REC_SIZE);
    if (record_write(32) != 0) return -1;
    first = 0;
    if (store_verify(&first) != 0) {
        print("store: restore failed\n");
        return -1;
    }
    print("store: verify ok ");
    put_dec(g_count);
    print("/");
    put_dec(g_count);
    print("\n");

    /* 9. reading past the count is refused, even though the orphan record at
     * that index is well formed on disk - it is simply not committed. */
    {
        u32 length = 0;
        if (store_read(g_count, spare, &length) == 0) {
            print("store: read past the count was allowed\n");
            return -1;
        }
        print("store: out of range ");
        put_dec(g_count);
        print("\n");
    }

    /* 10. the capacity guard. Tested at the boundary rather than by filling
     * 8 MB: the harness stops the VM after eight seconds, and a test that
     * cannot finish is worse than one that says where it stopped. */
    saved = g_count;
    g_count = STORE_CAPACITY;
    if (store_append_generated() >= 0) {
        print("store: append beyond capacity was allowed\n");
        g_count = saved;
        return -1;
    }
    print("store: full ");
    put_dec(STORE_CAPACITY);
    print("\n");
    g_count = saved;

    /* 11. rebuild from scratch and require identical bytes */
    if (store_format() != 0) return -1;
    for (i = 0; i < 64; i++) {
        if (store_append_generated() < 0) return -1;
    }
    store_probe_last_sector();

    region_second = region_fnv();
    if (region_second == region_first) {
        print("store: replay identical\n");
    } else {
        print("store: replay DIFFERS 0x");
        put_hex(region_first);
        print(" vs 0x");
        put_hex(region_second);
        print("\n");
        return -1;
    }

    return 0;
}
