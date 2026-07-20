from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "assets" / "demo"
OUTPUT = ASSET_DIR / "ic-copilot-aapl-demo.gif"
THUMBNAIL = ASSET_DIR / "ic-copilot-aapl-thumbnail.png"

FRAMES = [
    ("01-intro.png", "Evidence first. No invented thesis.", 3200),
    ("03-source-claim.png", "1. Start with the exact reported change", 3500),
    ("04-evidence-drawer.png", "2. Inspect the source, period, and citation", 4000),
    ("06-causal-graph-detail.png", "3. Score every link in the causal thesis", 4000),
    ("09-peer-checks.png", "4. Test the driver against peer operating metrics", 4000),
    ("10-reverse-dcf.png", "5. Reverse-engineer what the market price assumes", 4000),
    ("14-bull-bear-judge.png", "6. Separate bull, bear, accepted, and unproven", 4500),
    ("01-intro.png", "IC Copilot | Open-source, auditable IC research", 3500),
]


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _caption_frame(source: Path, caption: str) -> Image.Image:
    image = Image.open(source).convert("RGB")
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    bar_height = 82
    draw.rectangle((0, 0, width, bar_height), fill=(8, 13, 22, 238))
    draw.rectangle((0, bar_height - 4, width, bar_height), fill=(37, 170, 160, 255))
    draw.text((42, 20), caption, font=_font(34, bold=True), fill=(248, 250, 252, 255))

    footer = "Frozen AAPL demo | Illustrative research workflow | Not investment advice"
    footer_font = _font(18)
    footer_box = draw.textbbox((0, 0), footer, font=footer_font)
    footer_width = footer_box[2] - footer_box[0]
    footer_height = 38
    draw.rounded_rectangle(
        (width - footer_width - 46, height - footer_height - 16, width - 18, height - 14),
        radius=8,
        fill=(8, 13, 22, 225),
    )
    draw.text(
        (width - footer_width - 32, height - footer_height - 7),
        footer,
        font=footer_font,
        fill=(203, 213, 225, 255),
    )
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("P", palette=Image.Palette.ADAPTIVE)


def main() -> None:
    rendered = [_caption_frame(ASSET_DIR / filename, caption) for filename, caption, _ in FRAMES]
    durations = [duration for _, _, duration in FRAMES]
    rendered[0].save(
        OUTPUT,
        save_all=True,
        append_images=rendered[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    rendered[0].convert("RGB").save(THUMBNAIL, optimize=True)
    print(f"Created {OUTPUT}")
    print(f"Created {THUMBNAIL}")


if __name__ == "__main__":
    main()
