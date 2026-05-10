"""Generate fake Korean pharmaceutical bottle labels as PNG textures.

Three label styles (distribution tuned to match real Korean pharma observed
in screenshot/IMG_1785.png):
  - standard  (~60%): white bg, thin colored frame, hangul brand, dense filler
                      text, small corner barcode
  - dense     (~25%): white bg, multi-column text, thin dividers
  - fullcolor (~15%): full saturated background (red/navy/green) with white
                      text (like the 콜민-S bottle)

No Rx symbol, no warning triangle, no wide colored top band — none of those
appear on the real bottles.

Usage: python scripts/gen_fake_labels.py --n 40
"""
import argparse
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---------- content pools ----------

KOR_SYLLABLES = list(
    "가간감강건계고과구권그극근기남녀노다대덕도동두라로리마목문미바반백복분"
    "산상생서석선성세소송수숙순시식신아안약양어연영오용우원위유은의이인일"
    "자작장재전정조종주중지진차천철청초치카코콘타토파판편표풍피하한해향"
    "현형호화황회후희록렬령례린립마만말망매면명모무"
)

FAKE_BRANDS_EN = [
    "RELIEVIN", "CARDOPHAR", "NEUROGEN", "LUNAXIL", "ZYMOPREX",
    "HEPATONE", "GASTROMYL", "FERRIVIT", "PULMOCARE", "ORALIX",
    "DERMACOR", "CALMAXIN", "VITAMIX", "OSTEOLIN", "BRONCHEX",
]
KOR_SUFFIXES = ["정", "캡슐", "시럽", "과립", "현탁액", "산", "드롭", "연질캡슐", "필름코팅정"]
DOSAGES = ["100mg", "250mg", "500mg", "5mg/ml", "10mg/ml", "1000mg", "75mg"]
COUNT_LABELS = ["30정", "60정", "100정", "120ml", "200ml", "30캡슐", "50정", "90정"]
MFR_PREFIXES = ["(주)", ""]
MFR_SUFFIXES = ["제약", "파마", "약품", "바이오", "생명과학"]

FRAME_COLORS = [
    (0x1E, 0x3A, 0x8A),   # navy
    (0x0E, 0x76, 0x4E),   # pharma green
    (0xB0, 0x1F, 0x1F),   # medical red
    (0x7C, 0x2D, 0x8F),   # purple
    (0x0C, 0x79, 0xB8),   # cyan blue
    (0x8D, 0x6E, 0x2F),   # brown/gold
    (0x40, 0x40, 0x40),   # near-black
]
FULLCOLOR_BGS = [
    (0xB5, 0x24, 0x24),   # red (like 콜민-S)
    (0x15, 0x3E, 0x90),   # navy
    (0x0F, 0x6B, 0x4A),   # green
    (0x6B, 0x2A, 0x7A),   # purple
    (0x1A, 0x1A, 0x1A),   # near-black
]

KOR_FONT = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"
KOR_FONT_REG = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"
EN_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
EN_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
EN_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


# ---------- content helpers ----------

def kor_str(rng: random.Random, n: int) -> str:
    return "".join(rng.choices(KOR_SYLLABLES, k=n))


def fake_korean_brand(rng: random.Random) -> str:
    return kor_str(rng, rng.choice([2, 3, 3, 4])) + rng.choice(KOR_SUFFIXES)


def fake_manufacturer(rng: random.Random) -> str:
    return rng.choice(MFR_PREFIXES) + kor_str(rng, rng.choice([2, 3])) + rng.choice(MFR_SUFFIXES)


def filler_line(rng: random.Random, n: int) -> str:
    """Fake dense caution text — single line."""
    return kor_str(rng, n)


# ---------- drawing helpers ----------

def tt(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def draw_barcode(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, rng: random.Random):
    cursor = 0
    while cursor < w:
        bw = rng.choice([2, 2, 3, 3, 4, 5])
        if cursor + bw > w:
            break
        if rng.random() < 0.55:
            draw.rectangle([x + cursor, y, x + cursor + bw - 1, y + h], fill="black")
        cursor += bw


def draw_corner_barcode(draw, W, H, rng, fill=(80, 80, 80)):
    """Always bottom-right so the manufacturer text (bottom-left) never collides."""
    bar_w = rng.randint(120, 170)
    bar_h = rng.randint(28, 42)
    margin_x = 24
    margin_y = 44
    x = W - bar_w - margin_x
    y = H - bar_h - margin_y
    draw_barcode(draw, x, y, bar_w, bar_h, rng)
    lot = f"{rng.randint(10000, 99999)}"
    f = tt(EN_MONO, 14)
    draw.text((x, y + bar_h + 2), f"LOT {lot}", fill=fill, font=f)


def draw_thin_divider(draw, x1, x2, y, color=(150, 150, 150), width=1):
    draw.line([(x1, y), (x2, y)], fill=color, width=width)


def draw_accent_box(draw, x, y, w, h, color, text, rng):
    """Small colored box with dosage/count text (like '500mg 30T')."""
    draw.rectangle([x, y, x + w, y + h], fill=color)
    f = tt(EN_BOLD, int(h * 0.45))
    draw.text((x + w // 2, y + h // 2), text, fill="white", font=f, anchor="mm")


# ---------- style: standard ----------

def style_standard(img, draw, W, H, rng: random.Random):
    frame_color = rng.choice(FRAME_COLORS)
    # thin frame border
    frame_px = rng.randint(3, 6)
    draw.rectangle([frame_px // 2, frame_px // 2, W - frame_px // 2 - 1, H - frame_px // 2 - 1],
                   outline=frame_color, width=frame_px)

    use_korean = rng.random() < 0.85
    pad = 40

    # Brand block (top-left or centered)
    align_left = rng.random() < 0.6
    if use_korean:
        brand = fake_korean_brand(rng)
        f_brand = tt(KOR_FONT, rng.randint(82, 108))
    else:
        brand = rng.choice(FAKE_BRANDS_EN)
        f_brand = tt(EN_BOLD, rng.randint(76, 100))

    brand_x = pad + 20 if align_left else W // 2
    anchor = "lt" if align_left else "mt"
    brand_y = 45
    draw.text((brand_x, brand_y), brand, fill=(30, 30, 30), font=f_brand, anchor=anchor)
    bb = draw.textbbox((brand_x, brand_y), brand, font=f_brand, anchor=anchor)

    # subtitle in colored accent text
    sub_y = bb[3] + 14
    sub_parts = [rng.choice(DOSAGES)]
    if rng.random() < 0.5:
        sub_parts.append(rng.choice(KOR_SUFFIXES))
    sub = " · ".join(sub_parts)
    f_sub = tt(KOR_FONT_REG, rng.randint(34, 46))
    draw.text((brand_x, sub_y), sub, fill=frame_color, font=f_sub, anchor=("lt" if align_left else "mt"))
    sub_bb = draw.textbbox((brand_x, sub_y), sub, font=f_sub, anchor=("lt" if align_left else "mt"))

    # thin divider below brand/subtitle
    div_y = sub_bb[3] + 18
    draw_thin_divider(draw, pad, W - pad, div_y, color=frame_color, width=2)

    # dense filler caution text block (multi-line hangul gibberish)
    f_body = tt(KOR_FONT_REG, rng.randint(22, 28))
    cur_y = div_y + 16
    n_lines = rng.randint(3, 5)
    for _ in range(n_lines):
        line = filler_line(rng, rng.randint(16, 30))
        draw.text((pad, cur_y), line, fill=(60, 60, 60), font=f_body)
        cur_y += f_body.size + 6
        if cur_y > H - 120:
            break

    # optional accent box with count (right side)
    if rng.random() < 0.55:
        box_w, box_h = rng.randint(110, 140), rng.randint(50, 70)
        box_x = W - box_w - pad
        box_y = H - box_h - 110
        draw_accent_box(draw, box_x, box_y, box_w, box_h, frame_color,
                        rng.choice(COUNT_LABELS), rng)

    # manufacturer line bottom
    f_mfr = tt(KOR_FONT_REG, 22)
    draw.text((pad, H - 48), fake_manufacturer(rng), fill=(80, 80, 80), font=f_mfr)

    # corner barcode
    draw_corner_barcode(draw, W, H, rng)


# ---------- style: dense ----------

def style_dense(img, draw, W, H, rng: random.Random):
    frame_color = rng.choice(FRAME_COLORS)
    pad = 36

    # thin top & bottom rules
    draw_thin_divider(draw, pad, W - pad, pad, color=frame_color, width=3)
    draw_thin_divider(draw, pad, W - pad, H - pad, color=frame_color, width=3)

    # left column: brand + subtitle
    use_korean = rng.random() < 0.9
    brand = fake_korean_brand(rng) if use_korean else rng.choice(FAKE_BRANDS_EN)
    f_brand = tt(KOR_FONT if use_korean else EN_BOLD, rng.randint(68, 88))
    draw.text((pad + 14, pad + 28), brand, fill=(25, 25, 25), font=f_brand)
    bb = draw.textbbox((pad + 14, pad + 28), brand, font=f_brand)

    f_sub = tt(KOR_FONT_REG, 36)
    sub = f"{rng.choice(DOSAGES)}   {rng.choice(COUNT_LABELS)}"
    draw.text((pad + 14, bb[3] + 10), sub, fill=frame_color, font=f_sub)

    # center divider (vertical)
    mid_x = W // 2 + rng.randint(-40, 40)
    draw.line([(mid_x, pad + 10), (mid_x, H - pad - 10)], fill=(180, 180, 180), width=1)

    # right column: dense text lines
    f_body = tt(KOR_FONT_REG, rng.randint(20, 26))
    cur_y = pad + 28
    for _ in range(rng.randint(6, 9)):
        line = filler_line(rng, rng.randint(10, 20))
        draw.text((mid_x + 16, cur_y), line, fill=(70, 70, 70), font=f_body)
        cur_y += f_body.size + 4

    # manufacturer line
    f_mfr = tt(KOR_FONT_REG, 22)
    draw.text((pad + 14, H - pad - 40), fake_manufacturer(rng),
              fill=(80, 80, 80), font=f_mfr)

    # small barcode in opposite corner
    draw_corner_barcode(draw, W, H, rng)


# ---------- style: full-color ----------

def style_fullcolor(img, draw, W, H, rng: random.Random):
    bg = rng.choice(FULLCOLOR_BGS)
    draw.rectangle([0, 0, W, H], fill=bg)

    # white thin inner border
    draw.rectangle([14, 14, W - 15, H - 15], outline=(255, 255, 255), width=2)

    # brand name (Korean dominant on this style)
    use_korean = rng.random() < 0.95
    brand = fake_korean_brand(rng) if use_korean else rng.choice(FAKE_BRANDS_EN)
    f_brand = tt(KOR_FONT if use_korean else EN_BOLD, rng.randint(120, 160))
    draw.text((W // 2, H // 2 - 40), brand, fill=(255, 255, 255), font=f_brand, anchor="mm")

    # subtitle
    f_sub = tt(KOR_FONT_REG, rng.randint(44, 58))
    sub = f"{rng.choice(DOSAGES)} · {rng.choice(KOR_SUFFIXES)}"
    draw.text((W // 2, H // 2 + 55), sub, fill=(240, 240, 240), font=f_sub, anchor="mm")

    # tiny white manufacturer line near bottom
    f_mfr = tt(KOR_FONT_REG, 22)
    draw.text((W // 2, H - 50), fake_manufacturer(rng), fill=(230, 230, 230),
              font=f_mfr, anchor="mm")

    # corner barcode (white bars on dark bg would be wrong — use a white card)
    card_w, card_h = 180, 56
    card_x = W - card_w - 30
    card_y = H - card_h - 86
    draw.rectangle([card_x, card_y, card_x + card_w, card_y + card_h], fill=(255, 255, 255))
    draw_barcode(draw, card_x + 10, card_y + 8, card_w - 20, 30, rng)
    f_lot = tt(EN_MONO, 13)
    draw.text((card_x + card_w // 2, card_y + card_h - 12),
              f"LOT {rng.randint(10000, 99999)}", fill=(60, 60, 60), font=f_lot, anchor="mm")


# ---------- entry point ----------

STYLES = [
    ("standard", style_standard, 0.60),
    ("dense",    style_dense,    0.25),
    ("fullcolor", style_fullcolor, 0.15),
]


def pick_style(rng: random.Random):
    r = rng.random()
    acc = 0.0
    for name, fn, w in STYLES:
        acc += w
        if r <= acc:
            return name, fn
    return STYLES[-1][0], STYLES[-1][1]


def generate_label(idx: int, out_dir: Path, rng: random.Random, size=(1024, 512)):
    W, H = size
    tint = rng.randint(248, 255)
    img = Image.new("RGB", (W, H), (tint, tint, tint - rng.randint(0, 4)))
    draw = ImageDraw.Draw(img)

    name, fn = pick_style(rng)
    fn(img, draw, W, H, rng)

    path = out_dir / f"label_{idx:03d}_{name}.png"
    img.save(path)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=40)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    out_dir = args.out or Path(__file__).resolve().parent.parent / "textures" / "labels"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Wipe old labels so we don't mix generations
    for old in out_dir.glob("label_*.png"):
        old.unlink()

    rng = random.Random(args.seed)
    counts = {"standard": 0, "dense": 0, "fullcolor": 0}
    for i in range(args.n):
        path = generate_label(i, out_dir, rng)
        for style in counts:
            if f"_{style}" in path.name:
                counts[style] += 1
                break

    print(f"\n[ok] {args.n} labels in {out_dir}")
    print(f"     styles: {counts}")


if __name__ == "__main__":
    main()
