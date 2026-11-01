"""Minimal QR code encoder (standard library only): byte mode, error correction level M, versions 1-10 (up to 213 bytes).

Used by the web UI to show the phone link in LAN mode. `svg(text)` returns a ready-to-embed SVG string.
"""
from __future__ import annotations

# version -> list of (codewords per block, data codewords per block), one entry per block, level M
BLOCKS = {
    1: [(26, 16)], 2: [(44, 28)], 3: [(70, 44)], 4: [(50, 32)] * 2, 5: [(67, 43)] * 2, 6: [(43, 27)] * 4, 7: [(49, 31)] * 4,
    8: [(60, 38)] * 2 + [(61, 39)] * 2, 9: [(58, 36)] * 3 + [(59, 37)] * 2, 10: [(69, 43)] * 4 + [(70, 44)],
}
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}


def _rs_tables() -> tuple[list[int], list[int]]:
    exp, log, x = [0] * 512, [0] * 256, 1
    for i in range(255):
        exp[i], log[x] = x, i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


EXP, LOG = _rs_tables()


def _mul(a: int, b: int) -> int:
    return 0 if not a or not b else EXP[LOG[a] + LOG[b]]


def _rs_remainder(data: list[int], n: int) -> list[int]:
    gen = [1]
    for i in range(n):  # product of (x - alpha^i)
        nxt = [0] * (len(gen) + 1)
        for j, c in enumerate(gen):
            nxt[j] ^= c
            nxt[j + 1] ^= _mul(c, EXP[i])
        gen = nxt
    rem = [0] * n
    for b in data:
        f = b ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(n):
            rem[i] ^= _mul(gen[i + 1], f)
    return rem


def _codewords(data: bytes, version: int) -> list[int]:
    blocks = BLOCKS[version]
    cap = sum(d for _, d in blocks)
    bits = "0100" + format(len(data), "08b" if version < 10 else "016b") + "".join(format(b, "08b") for b in data)
    bits += "0" * min(4, cap * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    cw = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pad = 0
    while len(cw) < cap:
        cw.append((0xEC, 0x11)[pad % 2])
        pad += 1
    chunks, pos = [], 0
    for total, dcount in blocks:
        d = cw[pos:pos + dcount]
        pos += dcount
        chunks.append((d, _rs_remainder(d, total - dcount)))
    out = [b for i in range(max(len(d) for d, _ in chunks)) for d, _ in chunks if i < len(d) for b in (d[i],)]
    out += [e[i] for i in range(len(chunks[0][1])) for _, e in chunks]
    return out


def _penalty(m: list[list[bool]]) -> int:
    n, score = len(m), 0
    lines = [[("1" if c else "0") for c in row] for row in m] + [[("1" if m[y][x] else "0") for y in range(n)] for x in range(n)]
    for ln in lines:
        s = "".join(ln)
        run = 1
        for i in range(1, n):
            run = run + 1 if s[i] == s[i - 1] else 1
            if run == 5:
                score += 3
            elif run > 5:
                score += 1
        for pat in ("00001011101", "10111010000"):
            score += 40 * sum(1 for i in range(n - 10) if s[i:i + 11] == pat)
    for y in range(n - 1):
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(sum(r) for r in m)
    score += abs(dark * 100 // (n * n) - 50) // 5 * 10
    return score


def _matrix(cw: list[int], version: int) -> list[list[bool]]:
    n = version * 4 + 17
    mod = [[False] * n for _ in range(n)]
    fn = [[False] * n for _ in range(n)]

    def put(x: int, y: int, dark: bool) -> None:
        mod[y][x] = dark
        fn[y][x] = True

    for i in range(n):  # timing
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):  # finders + separators
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < n and 0 <= y < n:
                    put(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    pos = ALIGN[version]
    for cx in pos:
        for cy in pos:
            if (cx, cy) in ((6, 6), (6, pos[-1]), (pos[-1], 6)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    put(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
    def draw_format(mask: int, target: list[list[bool]]) -> None:
        rem = mask  # level M = 00, so the 5 data bits are just the mask
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = ((mask << 10) | rem) ^ 0x5412
        spots = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(14 - i, 8) for i in range(9, 15)]
        spots2 = [(n - 1 - i, 8) for i in range(8)] + [(8, n - 15 + i) for i in range(8, 15)]
        for i, (x, y) in enumerate(spots):
            target[y][x] = (bits >> i) & 1 == 1
        for i, (x, y) in enumerate(spots2):
            target[y][x] = (bits >> i) & 1 == 1
        target[n - 8][8] = True

    for x, y in [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(14 - i, 8) for i in range(9, 15)] + \
            [(n - 1 - i, 8) for i in range(8)] + [(8, n - 15 + i) for i in range(8, 15)] + [(8, n - 8)]:
        fn[y][x] = True
    if version >= 7:
        rem = version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = (version << 12) | rem
        for i in range(18):
            a, bb = n - 11 + i % 3, i // 3
            put(a, bb, (bits >> i) & 1 == 1)
            put(bb, a, (bits >> i) & 1 == 1)
    i = 0
    for right in range(n - 1, 0, -2):
        if right == 6:
            right = 5
        for vert in range(n):
            for j in range(2):
                x = right - j
                y = n - 1 - vert if ((right + 1) & 2) == 0 else vert
                if not fn[y][x] and i < len(cw) * 8:
                    mod[y][x] = (cw[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
    conds = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0, lambda x, y: (x + y) % 3 == 0,
             lambda x, y: (x // 3 + y // 2) % 2 == 0, lambda x, y: x * y % 2 + x * y % 3 == 0,
             lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0, lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0]
    best = None
    for mask, cond in enumerate(conds):
        m = [row[:] for row in mod]
        for y in range(n):
            for x in range(n):
                if not fn[y][x] and cond(x, y):
                    m[y][x] = not m[y][x]
        draw_format(mask, m)
        sc = _penalty(m)
        if best is None or sc < best[0]:
            best = (sc, m)
    return best[1]


def encode(text: str) -> list[list[bool]]:
    data = text.encode("utf-8")
    for v in range(1, 11):
        if len(data) <= sum(d for _, d in BLOCKS[v]) - (3 if v < 10 else 4):
            return _matrix(_codewords(data, v), v)
    raise ValueError("text too long for the built-in QR encoder (max ~210 bytes)")


def svg(text: str, border: int = 3) -> str:
    m = encode(text)
    n = len(m) + border * 2
    cells = "".join(f"M{x + border},{y + border}h1v1h-1z" for y, row in enumerate(m) for x, c in enumerate(row) if c)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{cells}" fill="#000"/></svg>')
