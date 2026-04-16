"""Generate placeholder Racetag icon at 1024x1024 and pack macOS .icns + Windows .ico.

Designed to be re-runnable. Outputs:
  racetag-source.png       (master, 1024x1024)
  racetag.iconset/*.png    (intermediate, macOS)
  racetag.icns             (macOS)
  racetag.ico              (Windows)

Replace this placeholder by dropping a 1024x1024 PNG at racetag-source.png
and re-running. No network access required.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "racetag-source.png"
ICONSET = HERE / "racetag.iconset"
ICNS = HERE / "racetag.icns"
ICO = HERE / "racetag.ico"

BG_TOP = (230, 57, 70)       # racing red
BG_BOTTOM = (168, 25, 38)    # deeper red
FG = (255, 255, 255)
SIZE = 1024
CORNER_RADIUS = 180


def find_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    grad = Image.new("RGB", (1, size), top)
    px = grad.load()
    for y in range(size):
        t = y / (size - 1)
        px[0, y] = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
    return grad.resize((size, size))


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=radius, fill=255)
    return mask


def draw_monogram(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    font = find_font(560)
    text = "RT"
    bbox = d.textbbox((0, 0), text, font=font, anchor="lt")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (SIZE - tw) / 2 - bbox[0]
    y = (SIZE - th) / 2 - bbox[1] - 20
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((x + 8, y + 12), text, font=font, fill=(0, 0, 0, 110))
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))
    img.alpha_composite(shadow)
    d.text((x, y), text, font=font, fill=FG + (255,))

    # Subtle underline accent
    bar_y = int(SIZE * 0.82)
    bar_h = int(SIZE * 0.02)
    bar_w = int(SIZE * 0.36)
    d.rounded_rectangle(
        [((SIZE - bar_w) / 2, bar_y), ((SIZE + bar_w) / 2, bar_y + bar_h)],
        radius=bar_h // 2,
        fill=FG + (255,),
    )


def build_source() -> Image.Image:
    base = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gradient = vertical_gradient(SIZE, BG_TOP, BG_BOTTOM).convert("RGBA")
    mask = rounded_mask(SIZE, CORNER_RADIUS)
    base.paste(gradient, (0, 0), mask)
    draw_monogram(base)
    return base


def write_iconset(src: Image.Image) -> None:
    ICONSET.mkdir(exist_ok=True)
    # Apple's required sizes and naming
    targets = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]
    for size, name in targets:
        resized = src.resize((size, size), Image.LANCZOS)
        resized.save(ICONSET / name, "PNG")


def write_icns() -> None:
    result = subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"iconutil failed: {result.stderr}")


def write_ico(src: Image.Image) -> None:
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    src.save(ICO, format="ICO", sizes=sizes)


def main() -> None:
    img = build_source()
    img.save(SOURCE, "PNG")
    write_iconset(img)
    write_icns()
    write_ico(img)
    print(f"Wrote {SOURCE.name}, {ICNS.name}, {ICO.name}")


if __name__ == "__main__":
    main()
