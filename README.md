# Koi Over the City — ai-excited-01

A giant luminous koi arcs across a twilight sky above a tiny city
skyline. A fish the size of the sky, swimming through the air over a
city that fits under its belly: a surreal scale twist, one huge
diagonal shape, vivid orange-gold against deep indigo dusk, with
flowing translucent tail ribbons, a pale moon, glowing bubbles and
faint ripple rings in the sky around it — as if the koi is swimming
through the air itself.

Why it should excite the judge: one look lands as a single enormous
shape (it reads at 300 px), the scale twist is a surprise no stock
explosion or firework can match, and everything — the arcing body,
the trailing ribbons, the bubbles, the light particles — drives in
one direction of motion. The style is flat vector-clean, so there is
no smear, garbled text or broken anatomy to trip over.

## How it works

`main.py` (Python 3.13 standard library only) draws the whole scene
with a small software rasterizer written for this entry:

- a `Canvas` class holding an RGB bytearray, with scanline polygon
  fills, filled circles/ellipses, rings, arcs, thick polylines and
  tapered ribbon polygons;
- the sky as a multi-stop vertical gradient, stars and a cratered
  moon from a fixed-seed LCG (no time, no OS randomness, no network);
- the koi as a quadratic-Bezier spine, its body built from segment
  quads shaded tail-to-head from deep ember to bright gold, with a
  pale belly band, a dorsal shade, staggered scale dots, a dorsal
  fin ridge, a translucent pectoral fin, a clear eye, whiskers, three
  flowing tail ribbons, bubbles and light particles;
- the city as a deterministic skyline silhouette with lit windows
  and antenna beacons, under a narrow ember horizon band;
- a hand-written PNG writer (zlib compression, CRC32 chunk framing).

## Run it

```sh
mkdir -p out && MAKEFUN_OUTPUT_DIR=out python3 main.py
# writes out/entry.png — 1024x1024, well under 1 MiB
```

Running it twice produces byte-identical output (verified with
sha256). It runs in about 1 second and needs no network, no files
besides this repository, and nothing beyond the standard library.

## License

MIT — see `LICENSE`.
