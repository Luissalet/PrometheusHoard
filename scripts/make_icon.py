"""Build Prometheus's icon from the family dragon.

    python scripts/make_icon.py [--src path/to/dragon-src.png] [--preview out.png]

Steps (the family recipe):
1. mask the yellow play button by HSV (and the dark halo around it) and fill it,
   so its triangle goes too;
2. flatten the picture to "dragon luminance on a flat background" and inpaint the
   button area on that flat map (cv2.inpaint TELEA), so no yellow or dark ring bleeds
   into the result;
3. recolour the dragon by luminance with a dark→light ramp (Galton: deep brass #3b2f0a → #e8cf84, around the accent #c9a227);
   the eye keeps a light colour; the background becomes the family's flat dark navy (the black
   rounded-square corners disappear);
4. compose a gold vector glyph ≈400 px centred at (627, 768): a Galton board, a triangle of pegs above bins whose columns of balls make a bell curve, with a dark outline like the other icons.

When dragon-src.png is not at hand, the family's finished icons are the source (the default when the shared
Icons folder is next to this repository, or with --family <Icons folder>): the dragon is the same in all of them
and only the glyph in the middle changes, so each icon's gold glyph (and its dark outline) is masked and every
pixel takes the dragon from the siblings whose glyph does not cover it (brightness matched between icons,
each weighted by its distance to its own glyph so the seams fade). No pixel of the dragon is guessed, so there are no inpainting smudges where the body passes behind
the glyph. A single sibling still works with --src <sibling>/app-icon.png (glyph area inpainted, may smudge).

Outputs: app-icon.png (1254²), client/public/icon-512.png, icon-192.png, favicon.ico (also copied to galton_hoard/static)
(16-256) and dist-icons/Prometheus hoard.png (for the shared Icons folder).
Needs: pillow, numpy, opencv-python-headless (requirements-icon.txt).
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SIZE = 1254
BACKGROUND = (1, 11, 27)  # the family's flat dark navy
RAMP_DARK = np.array([0x1c, 0x30, 0x04], dtype=np.float32)
RAMP_LIGHT = np.array([0xc4, 0xec, 0x7c], dtype=np.float32)
EYE_COLOR = np.array([0xFD, 0xEE, 0xEC], dtype=np.float32)
GOLD_TOP = (0xF8, 0xD8, 0x8C)
GOLD_BOTTOM = (0xE0, 0xA2, 0x42)
OUTLINE = (6, 10, 24)
GLYPH_CENTER = (627, 768)
GLYPH_SIZE = 400


FAMILY_MIN_ICONS = 5


def family_icons(folder: Path) -> list[Path]:
    """The shared Icons folder's finished family icons other than this app's own: same size, the family's flat
    navy in the corner (not a black rounded square) and a dragon that is not gold, so its glyph can be told apart."""
    found = []
    for path in sorted(folder.glob("*.png")):
        if path.name.lower().startswith("prometheus"):
            continue
        with Image.open(path) as img:
            if img.size != (SIZE, SIZE):
                continue
            rgb = np.array(img.convert("RGB"))
        corner = rgb[:60, :60].reshape(-1, 3).mean(axis=0)
        if not (corner[2] > 15 and corner.max() < 45):
            continue
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        ring = hsv[150:350, 250:1000]  # the head and the upper body, well away from any glyph
        body = ring[ring[..., 2] > 100]
        if body.size == 0 or 30 <= float(np.median(body[:, 0])) <= 50:
            continue
        found.append(path)
    return found


def default_family() -> Path | None:
    for folder in (ROOT.parent / "Icons", ROOT.parent / "icons"):
        if folder.is_dir() and len(family_icons(folder)) >= FAMILY_MIN_ICONS:
            return folder
    return None


def default_source() -> Path | None:
    for candidate in (ROOT.parent / "Icons" / "dragon-src.png", ROOT.parent / "icons" / "dragon-src.png", Path.home() / "icons" / "dragon-src.png",
                      ):
        if candidate.is_file():
            return candidate
    for sibling in sorted(ROOT.parent.glob("*/app-icon.png")):
        if sibling.parent != ROOT:
            return sibling
    return None


# ---------------------------------------------------------------------------
# dragon
# ---------------------------------------------------------------------------

def button_mask(rgb: np.ndarray) -> np.ndarray:
    """The yellow button, its triangle and the dark halo around it (uint8 0/255)."""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    yellow = cv2.inRange(hsv, (12, 90, 110), (40, 255, 255))
    yellow = cv2.morphologyEx(yellow, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(yellow, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(yellow)
    if contours:
        biggest = max(contours, key=cv2.contourArea)
        cv2.drawContours(filled, [biggest], -1, 255, thickness=cv2.FILLED)  # includes the triangle
    halo = cv2.dilate(filled, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (29, 29)))
    return halo


def eye_mask(rgb: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    cyan = cv2.inRange(hsv, (80, 120, 120), (105, 255, 255))
    cyan = cv2.morphologyEx(cyan, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return cv2.dilate(cyan, np.ones((5, 5), np.uint8))


def glyph_mask(rgb: np.ndarray) -> np.ndarray:
    """A finished family icon's gold glyph plus its dark outline (bool)."""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    gold = cv2.inRange(hsv, (12, 70, 120), (38, 255, 255))
    gold = cv2.morphologyEx(gold, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(gold)
    keep = np.zeros_like(gold)
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if 330 < x + w / 2 < 930 and 470 < y + h / 2 < 1060 and area > 30:  # the glyph sits in the middle
            keep[labels == i] = 255
    return cv2.dilate(keep, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (61, 61))) > 0


def family_dragon_map(folder: Path) -> tuple[np.ndarray, np.ndarray]:
    """The dragon's normalised brightness (0 = background, 1 = the dragon's light end, above 1.1 = the eye)
    rebuilt from the family's icons, and the eye mask."""
    maps, covers = [], []
    for path in family_icons(folder):
        rgb = np.array(Image.open(path).convert("RGB").resize((SIZE, SIZE), Image.LANCZOS))
        value = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[..., 2].astype(np.float32)
        bg = float(np.median(value[:60, :60]))
        maps.append(np.clip(value - bg, 0.0, None))
        covers.append(glyph_mask(rgb))
    if len(maps) < FAMILY_MIN_ICONS:
        raise SystemExit(f"need at least {FAMILY_MIN_ICONS} family icons in {folder}, found {len(maps)}")
    stack, covered = np.stack(maps), np.stack(covers)
    # Drop an icon whose dragon outline disagrees with the others (smudged or shifted when it was made).
    shape = stack > 60
    agreed = np.median(shape, axis=0) > 0.5
    edge_band = cv2.dilate(agreed.astype(np.uint8), np.ones((25, 25), np.uint8)) > 0
    disagreement = np.array([(shape[i] != agreed)[edge_band & ~covered[i]].mean() for i in range(len(maps))])
    keep = disagreement <= max(0.015, 2.5 * float(np.median(disagreement)))
    stack, covered = stack[keep], covered[keep]
    if len(stack) < FAMILY_MIN_ICONS - 2:
        raise SystemExit(f"too few family icons agree on the dragon in {folder}")
    # One gain per icon, measured where no glyph covers any of them, so all icons agree on the brightness.
    clear = ~covered.any(axis=0)
    reference = np.median(stack, axis=0)
    body = clear & (reference > 60)
    gains = np.array([np.median(reference[body] / np.maximum(m[body], 1.0)) for m in stack], np.float32)
    stack = stack * gains[:, None, None]
    top = float(np.percentile(reference[body & (reference < np.percentile(reference[body], 99.5))], 98))
    stack = np.clip(stack / max(1.0, top), 0.0, 1.3)
    # Each icon counts less the closer a pixel is to its glyph, so the seams between icons fade out;
    # a remnant of a glyph just outside its mask cannot show.
    weights = np.stack([np.clip(cv2.distanceTransform((~c).astype(np.uint8), cv2.DIST_L2, 5) / 60.0, 0.0, 1.0) ** 2
                        for c in covered])

    total = weights.sum(axis=0)
    t = np.where(total > 1e-3, (stack * weights).sum(axis=0) / np.maximum(total, 1e-3), 0.0).astype(np.float32)
    # Near the glyph only a few icons show the body, and where their edges disagree the outline gets a bite:
    # close the shape there (never elsewhere, so the horns keep their sharp corners).
    sparse = (~covered).sum(axis=0) <= 3
    closed = cv2.morphologyEx(t, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)))
    bite = sparse & (t < closed - 0.2)
    t = np.where(bite, np.minimum(closed, 1.0), t).astype(np.float32)
    eye = cv2.dilate(((t > 1.1) * 255).astype(np.uint8), np.ones((3, 3), np.uint8))
    return t, eye


def recolour_map(t_map: np.ndarray, eye: np.ndarray) -> Image.Image:
    """Recolour a normalised dragon map with the ramp on the family's flat navy."""
    alpha = np.clip((t_map - 0.15) / 0.3, 0.0, 1.0)
    body = t_map[(alpha > 0.9) & (eye == 0)]
    lo, hi = (np.percentile(body, 2), np.percentile(body, 98)) if body.size else (0.5, 1.0)
    t = np.clip((t_map - lo) / max(1e-3, hi - lo), 0.0, 1.0)[..., None]
    colour = RAMP_DARK * (1 - t) + RAMP_LIGHT * t
    eye_f = (cv2.GaussianBlur(eye, (5, 5), 0).astype(np.float32) / 255.0)[..., None]
    colour = colour * (1 - eye_f) + EYE_COLOR * eye_f
    background = np.array(BACKGROUND, dtype=np.float32)
    a = alpha[..., None]
    out = background * (1 - a) + colour * a
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def recolour_dragon(src: Image.Image) -> Image.Image:
    rgb = np.array(src.convert("RGB").resize((SIZE, SIZE), Image.LANCZOS))
    button = button_mask(rgb)
    # A sibling's finished icon carries its own glyph (outlined, recoloured): clear the whole glyph area too.
    cx, cy = GLYPH_CENTER
    half = int(GLYPH_SIZE * 0.52)
    cv2.rectangle(button, (cx - half, cy - half), (cx + half, cy + half), 255, thickness=cv2.FILLED)
    eye = eye_mask(rgb)
    lum = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    lum[eye > 0] = 200.0  # the eye counts as dragon (light) for the flat map
    # Flatten: dragon luminance on a flat background level, so inpainting cannot pick up
    # the button's yellow or the rounded square's black corners.
    bg_level = float(np.median(lum[(lum < 40) & (button == 0)])) if np.any((lum < 40) & (button == 0)) else 12.0
    flat = np.where(lum > 45, lum, bg_level).astype(np.float32)
    flat8 = np.clip(flat, 0, 255).astype(np.uint8)
    inpainted = cv2.inpaint(flat8, button, 21, cv2.INPAINT_TELEA).astype(np.float32)
    # Alpha of the dragon: smooth ramp on luminance (antialiased edges).
    alpha = np.clip((inpainted - 50.0) / 40.0, 0.0, 1.0)
    dragon = inpainted[alpha > 0.9]
    lo, hi = (np.percentile(dragon, 2), np.percentile(dragon, 98)) if dragon.size else (90.0, 220.0)
    t = np.clip((inpainted - lo) / max(1.0, hi - lo), 0.0, 1.0)[..., None]
    colour = RAMP_DARK * (1 - t) + RAMP_LIGHT * t
    eye_f = (cv2.GaussianBlur(eye, (5, 5), 0).astype(np.float32) / 255.0)[..., None]
    colour = colour * (1 - eye_f) + EYE_COLOR * eye_f
    background = np.array(BACKGROUND, dtype=np.float32)
    a = alpha[..., None]
    out = background * (1 - a) + colour * a
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


# ---------------------------------------------------------------------------
# glyph: a flame (Prometheus's fire) over three small blocks — the Sparks
# ---------------------------------------------------------------------------

def _finish(mask: np.ndarray, big: int, box: int, u: float) -> Image.Image:
    """Gold fill with a vertical gradient and the family's dark outline around the glyph."""
    outline = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(26 * u), int(26 * u))))
    top, bottom = np.array(GOLD_TOP, np.float32), np.array(GOLD_BOTTOM, np.float32)
    ramp = np.linspace(0, 1, big, dtype=np.float32)[:, None, None]
    gold = np.broadcast_to(top * (1 - ramp) + bottom * ramp, (big, big, 3)).astype(np.uint8)
    layer = np.zeros((big, big, 4), np.uint8)
    layer[..., :3] = OUTLINE
    layer[outline > 0, 3] = 255
    layer[mask > 0, :3] = gold[mask > 0]
    layer[mask > 0, 3] = 255
    return Image.fromarray(layer, "RGBA").resize((box, box), Image.LANCZOS)


def _flame(cx: float, base_y: float, w: float, h: float, lean: float, n: int = 160) -> list[tuple[float, float]]:
    """A flame outline: round bottom, sides narrowing to a tip that leans to one side."""
    pts = []
    for i in range(n + 1):
        t = i / n                       # 0 -> 1 around the outline, starting at the tip on the right side
        a = math.pi * 2 * t
        # a teardrop: x = sin(a) * width(t), y from base to tip
        y = -math.cos(a / 2) ** 2 * h
        y_norm = -y / h                 # 0 at the base, 1 at the tip
        width = w * (1 - y_norm) ** 0.85 * (0.55 + 0.45 * math.sin(math.pi * min(1.0, 1.15 * (1 - y_norm))))
        x = math.sin(a) * width + lean * y_norm ** 2
        pts.append((cx + x, base_y + y))
    return pts


def glyph_layer(scale: int = 4) -> Image.Image:
    box = int(GLYPH_SIZE * 1.4)
    big = box * scale
    c = big / 2
    u = GLYPH_SIZE * scale / 400.0

    def P(x, y):
        return (c + x * u, c + (y - 10) * u)

    m = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(m)
    outer = [P(x, y) for x, y in _flame(0, 108, 150, 350, 46)]
    d.polygon(outer, fill=255)
    inner = [P(x, y) for x, y in _flame(4, 100, 68, 175, -20)]
    d.polygon(inner, fill=0)
    core = [P(x, y) for x, y in _flame(2, 92, 28, 82, 6)]
    d.polygon(core, fill=255)
    # three Sparks under the fire, joined by a bar (the cables)
    for k in (-1, 0, 1):
        x0 = k * 104 - 44
        d.rounded_rectangle([P(x0, 130), P(x0 + 88, 182)], radius=int(10 * u), fill=255)
        d.rectangle([P(x0 + 14, 152), P(x0 + 50, 160)], fill=0)
        d.ellipse([P(x0 + 64, 150), P(x0 + 74, 160)], fill=0)
    d.rectangle([P(-150, 196), P(150, 206)], fill=255)
    mask = (np.array(m) > 0).astype(np.uint8) * 255
    return _finish(mask, big, box, u)


def compose(src: Image.Image | Path) -> Image.Image:
    base = (recolour_map(*family_dragon_map(src)) if isinstance(src, Path) else recolour_dragon(src)).convert("RGBA")
    glyph = glyph_layer()
    x = GLYPH_CENTER[0] - glyph.width // 2
    y = GLYPH_CENTER[1] - glyph.height // 2
    base.alpha_composite(glyph, (x, y))
    return base


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--src", type=Path, default=None, help="The family dragon (dragon-src.png) or a sibling app icon")
    parser.add_argument("--family", type=Path, default=None, help="The shared Icons folder (rebuild the dragon from the family)")
    parser.add_argument("--preview", type=Path, default=None, help="Also write a 400 px preview here")
    args = parser.parse_args()
    family = args.family or (None if args.src else default_family())
    if family is not None:
        src_path = family
        icon = compose(family)
    else:
        src_path = args.src or default_source()
        if src_path is None or not src_path.is_file():
            print("no source: pass --family <Icons folder> or --src dragon-src.png")
            return 2
        icon = compose(Image.open(src_path))
    icon.save(ROOT / "app-icon.png", optimize=True)
    public = ROOT / "client" / "public"
    public.mkdir(parents=True, exist_ok=True)
    icon.resize((512, 512), Image.LANCZOS).save(public / "icon-512.png", optimize=True)
    icon.resize((192, 192), Image.LANCZOS).save(public / "icon-192.png", optimize=True)
    icon.convert("RGBA").save(public / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    static = ROOT / "prometheus_hoard" / "static"  # the built client served by the app, kept in step with public/
    if static.is_dir():
        for name in ("icon-512.png", "icon-192.png", "favicon.ico"):
            (static / name).write_bytes((public / name).read_bytes())
    dist = ROOT / "dist-icons"
    dist.mkdir(exist_ok=True)
    icon.save(dist / "Prometheus hoard.png", optimize=True)
    if args.preview:
        icon.resize((400, 400), Image.LANCZOS).convert("RGB").save(args.preview)
    print(f"icon written from {src_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
