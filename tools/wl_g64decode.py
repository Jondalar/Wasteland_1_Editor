#!/usr/bin/env python3
"""
Standalone G64 -> decoded sectors. Reads the .g64 bitstream, GCR-decodes each
track (1..35) into {sector: 256B}. Validated byte-for-byte against the existing
side-1 rawscan/ (ground truth). Used for the asset-baseline pass-2 (originals).

Usage:
  python3 wl_g64decode.py <image.g64> validate <rawscan_dir>   # gate vs ground truth
  python3 wl_g64decode.py <image.g64> dump <out_dir>           # write tNsP.bin per sector
"""
import sys, os, struct

# GCR 5-bit -> 4-bit nibble (invalid groups -> None)
GCR = {0x0a:0,0x0b:1,0x12:2,0x13:3,0x0e:4,0x0f:5,0x16:6,0x17:7,
       0x09:8,0x19:9,0x1a:10,0x1b:11,0x0d:12,0x1d:13,0x1e:14,0x15:15}
SPT = [21]*17 + [19]*7 + [18]*6 + [17]*5   # sectors/track 1..35

def track_bits(raw, off):
    if off == 0: return None
    ln = raw[off] | (raw[off+1] << 8)
    data = raw[off+2 : off+2+ln]
    bits = bytearray()
    for b in data:
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)
    return bits

def gcr_decode_bytes(bits, start, nbytes):
    """decode nbytes from bit position start (10 GCR bits per decoded byte)."""
    out = bytearray(); p = start; n = len(bits)
    for _ in range(nbytes):
        nib = []
        for _ in range(2):
            v = 0
            for _ in range(5):
                v = (v << 1) | bits[p % n]; p += 1
            d = GCR.get(v)
            if d is None: return None, p
            nib.append(d)
        out.append((nib[0] << 4) | nib[1])
    return bytes(out), p

def decode_track(bits):
    """find sync->block, pair header(0x08)+data(0x07) -> {sector: 256B}."""
    if not bits: return {}
    n = len(bits); secs = {}; pending_hdr = None
    i = 0; ones = 0
    # scan for sync runs (>=10 ones), decode the block right after
    seen = 0
    while seen < n + 80:
        bit = bits[i % n]
        if bit == 1: ones += 1
        else:
            if ones >= 10:
                blk, _ = gcr_decode_bytes(bits, i % n, 1)   # peek block id
                if blk is not None and blk[0] == 0x08:
                    hdr, _ = gcr_decode_bytes(bits, i % n, 8)
                    if hdr: pending_hdr = hdr
                elif blk is not None and blk[0] == 0x07:
                    dat, _ = gcr_decode_bytes(bits, i % n, 257)
                    if dat and pending_hdr is not None:
                        sec = pending_hdr[2]
                        secs.setdefault(sec, bytes(dat[1:257]))
                        pending_hdr = None
            ones = 0
        i += 1; seen += 1
    return secs

def decode_g64(path, maxtrack=35):
    raw = open(path, 'rb').read()
    assert raw[:8] == b'GCR-1541', 'not a G64'
    nht = raw[9]
    offs = struct.unpack_from('<%dI' % nht, raw, 12)
    out = {}
    for t in range(1, maxtrack+1):
        ht = (t-1)*2          # full-track half-track index
        if ht >= nht: break
        bits = track_bits(raw, offs[ht])
        out[t] = decode_track(bits) if bits else {}
    return out

if __name__ == '__main__':
    img, mode = sys.argv[1], sys.argv[2]
    dec = decode_g64(img)
    if mode == 'validate':
        rs = sys.argv[3]; ok = miss = bad = 0; bads = []
        for t in range(1, 36):
            for p in range(SPT[t-1]):
                fn = f'{rs}/t{t}s{p}.bin'
                if not os.path.exists(fn): continue
                ref = open(fn, 'rb').read()[:256]
                got = dec.get(t, {}).get(p)
                if got is None: miss += 1; bads.append(f't{t}s{p}:missing'); continue
                if got == ref: ok += 1
                else: bad += 1; bads.append(f't{t}s{p}:diff')
        print(f'validate {img.split("/")[-1]}: match={ok} miss={miss} diff={bad}')
        if bads: print('  first 20:', bads[:20])
    elif mode == 'dump':
        out = sys.argv[3]; os.makedirs(out, exist_ok=True); w = 0
        for t in dec:
            for p, b in dec[t].items():
                open(f'{out}/t{t}s{p}.bin', 'wb').write(b); w += 1
        print(f'dumped {w} sectors -> {out}')
