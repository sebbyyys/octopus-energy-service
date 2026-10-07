"""Reproducibly draw an original energy-octopus icon, not supplier branding."""

from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    """Generate a transparent, antialiased 256px icon using simple geometry."""
    scale = 4
    image = Image.new("RGBA", (256 * scale, 256 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def points(items):
        return [(x * scale, y * scale) for x, y in items]

    def box(items):
        return tuple(item * scale for item in items)

    ink = "#24234A"
    sea = "#25B9AE"
    draw.ellipse(box((8, 8, 248, 248)), fill=ink)
    for coordinates in (
        [(83, 130), (51, 164), (35, 150)],
        [(96, 145), (68, 190), (45, 181)],
        [(113, 157), (99, 210), (72, 205)],
        [(123, 162), (120, 217), (109, 219)],
        [(133, 162), (136, 217), (147, 219)],
        [(143, 157), (157, 210), (184, 205)],
        [(160, 145), (188, 190), (211, 181)],
        [(173, 130), (205, 164), (221, 150)],
    ):
        draw.line(points(coordinates), fill=sea, width=12 * scale, joint="curve")
        x, y = coordinates[-1]
        draw.ellipse(box((x - 6, y - 6, x + 6, y + 6)), fill=sea)
    draw.ellipse(box((70, 49, 186, 173)), fill=sea)
    draw.ellipse(box((91, 97, 107, 116)), fill=ink)
    draw.ellipse(box((149, 97, 165, 116)), fill=ink)
    draw.polygon(
        points([(136, 61), (113, 91), (129, 91), (121, 115), (146, 81), (130, 81)]), fill="#FFE37A"
    )
    output = (
        Path(__file__).resolve().parents[2]
        / "custom_components/octopus_energy_service/brand/icon.png"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    image.resize((256, 256), Image.Resampling.LANCZOS).save(output)
    print(f"Created {output}: 256x256 original RGBA PNG")


if __name__ == "__main__":
    main()
