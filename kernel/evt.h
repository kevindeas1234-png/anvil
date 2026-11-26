#ifndef AOS_EVT_H
#define AOS_EVT_H

/* Machine-readable event records (M20). One per line:
 *
 *   evt id=store.pack seq=41 bytes_in=1024 bytes_out=10 writes=512 verdict=packed chk=0x1a2b3c4d
 *
 * The checksum is FNV-1a 32 over the bytes of the line up to, but not including,
 * " chk=" — so a truncated line cannot still verify, and a parser needs no
 * agreement with the emitter about field order beyond "as emitted".
 *
 * Called chk= and not hash= because several events carry a named field `hash`
 * (the payload hash). One line cannot have two fields with the same key.
 *
 * Kept out of line in kernel.c on purpose: this project once grew the console
 * writer and the compiler inlined it into hundreds of call sites, taking .text
 * from 24 KB to 98 KB and overflowing the boot window.
 */
void evt_begin(const char *id);
void evt_u32(const char *key, unsigned value);
void evt_hex32(const char *key, unsigned value);
void evt_str(const char *key, const char *value);
void evt_end(void);

#endif
