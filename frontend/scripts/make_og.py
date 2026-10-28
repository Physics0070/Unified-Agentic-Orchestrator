"""Render public/og.png - the social card for a shared link.

Same double helix as the landing hero (src/ParticleField.tsx), drawn once at
1200x630 so the card is a still of the real visual rather than a separate
asset that drifts away from it. Re-run after changing the hero:

    python3 scripts/make_og.py

Needs Pillow only. The font falls back to Helvetica because Inter is loaded
from Google Fonts at runtime and is not on disk.
"""
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
TAU = math.pi * 2
SPECTRUM = [
    '#8052ff', '#8052ff', '#9a6bff', '#6d3dff',
    '#ffb829', '#ffcf6b', '#15846e', '#1fb894',
    '#e85aff', '#4f86ff',
]
FONT = '/System/Library/Fonts/HelveticaNeue.ttc'

# Left of where the helix starts, with a gutter.
HEADLINE_MAX_W = 610

# Fixed seed: the card should not change every time someone runs the build.
random.seed(42)


def jitter(k: float) -> float:
    return (random.random() + random.random() + random.random() - 1.5) * k


def triangle(d: ImageDraw.ImageDraw, x: float, y: float, s: float, colour: str, alpha: int):
    rgb = tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))
    d.polygon(
        [(x, y - s), (x + s * 0.87, y + s * 0.6), (x - s * 0.87, y + s * 0.6)],
        outline=rgb + (alpha,),
    )


def main() -> None:
    img = Image.new('RGB', (W, H), '#000000')
    layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # Ambient drift.
    for _ in range(170):
        triangle(d, random.uniform(0, W), random.uniform(0, H),
                 random.uniform(1, 3), random.choice(SPECTRUM),
                 int(random.uniform(0.10, 0.40) * 255))

    # The helix, rotated to the same angle the page settles at.
    cx, cy, scale = W * 0.76, H / 2, 250
    turns, height, radius, theta = 3.05, 2.05, 0.58, 0.62
    cs, sn = math.cos(theta), math.sin(theta)

    for _ in range(2600):
        t = random.random()
        a = t * turns * TAU
        y = (t - 0.5) * height + jitter(0.012)
        roll = random.random()
        if roll < 0.8:                                   # one of the backbones
            off = 0 if roll < 0.4 else math.pi
            px = math.cos(a + off) * radius + jitter(0.055)
            pz = math.sin(a + off) * radius + jitter(0.055)
        else:                                            # a rung across them
            u = random.random()
            ax, az = math.cos(a) * radius, math.sin(a) * radius
            bx, bz = math.cos(a + math.pi) * radius, math.sin(a + math.pi) * radius
            px = ax + (bx - ax) * u + jitter(0.02)
            pz = az + (bz - az) * u + jitter(0.02)

        rx = px * cs + pz * sn
        rz = -px * sn + pz * cs
        depth = (rz + 0.8) / 1.6
        triangle(d, cx + rx * scale, cy + y * scale,
                 random.uniform(1.1, 3.4) * (0.55 + depth * 0.75),
                 random.choice(SPECTRUM),
                 int(min(1.0, 0.26 + depth * 0.74) * 255))

    img = Image.alpha_composite(img.convert('RGBA'), layer).convert('RGB')
    d = ImageDraw.Draw(img)

    label = ImageFont.truetype(FONT, 20, index=10)      # Medium
    # The page uses weight 200 here, but a social card is read at thumbnail
    # size where ultralight disappears. Light is the closest legible step.
    body = ImageFont.truetype(FONT, 24, index=7)        # Light

    # The helix occupies the right of the frame, so the headline has to stop
    # short of it. Shrink until the longest line clears, rather than guessing
    # a size that happens to fit this wording and breaks on the next edit.
    lines = ['Ask anything.', 'Then see what it cost.']
    size = 74
    while size > 40:
        display = ImageFont.truetype(FONT, size, index=0)
        if max(d.textlength(ln, font=display) for ln in lines) <= HEADLINE_MAX_W:
            break
        size -= 2

    d.text((78, 198), 'MULTI-AGENT ORCHESTRATION', font=label, fill='#ffb829')
    d.text((74, 250), lines[0], font=display, fill='#ffffff')
    d.text((74, 250 + size * 1.14), lines[1], font=display, fill='#ffffff')
    d.text((78, 442), 'A council of models that reviews itself anonymously —',
           font=body, fill='#bdbdbd')
    d.text((78, 476), 'and reports when it was overkill.', font=body, fill='#bdbdbd')

    out = Path(__file__).resolve().parent.parent / 'public' / 'og.png'
    out.parent.mkdir(exist_ok=True)
    img.save(out, optimize=True)
    print(f'wrote {out} ({out.stat().st_size // 1024} kB)')


if __name__ == '__main__':
    main()
