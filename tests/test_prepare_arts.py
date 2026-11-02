import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import prepare_arts as arts  # noqa: E402


def test_axis_map_is_identity_in_the_middle_and_covers_the_whole_art():
    coords = arts.axis_map(100, 80, 10, 10, 20, 20)
    assert coords[0] == pytest.approx(0, abs=0.6)
    assert coords[-1] == pytest.approx(80, abs=0.6)
    assert np.all(np.diff(coords) >= 0)  # never folds back
    middle = np.arange(35, 65)
    assert coords[middle] == pytest.approx(middle - 10 + 0.5, abs=1e-3)  # 1:1, just shifted


def test_axis_map_stretches_only_the_edge_bands():
    coords = arts.axis_map(120, 80, 20, 20, 10, 10)
    step = np.diff(coords)
    assert step[:15].min() < 1  # the first band is stretched (source advances slowly)
    assert step[40:80] == pytest.approx(1.0, abs=1e-3)  # the middle is untouched


def test_content_rows_finds_the_logo_and_the_footer():
    a = np.full((200, 100, 3), 240, dtype=np.uint8)
    a[40:60, 30:70] = 10  # logo
    a[170:180, 30:70] = 10  # footer text
    assert arts.content_rows(a) == (40, 179)


def _art(w: int, h: int, color=(240, 235, 225)) -> "Image.Image":
    img = Image.new("RGB", (w, h), color)
    img.paste((20, 20, 20), (w // 3, h // 10, 2 * w // 3, h // 5))  # a "logo"
    return img


def test_every_layout_produces_a_story_sized_jpeg_ready_image():
    for size in [(720, 1280), (900, 1600), (960, 1280), (853, 1280), (1024, 1280)]:
        out = arts.compose(_art(*size))
        assert out.size == (arts.WIDTH, arts.HEIGHT) and out.mode == "RGB"


def test_layout_is_chosen_by_proportion_and_can_be_forced():
    tall, wide = _art(720, 1280), _art(960, 1280)
    assert arts.compose(tall).getpixel((540, 5)) == arts.compose(tall, "bleed").getpixel((540, 5))
    forced = arts.compose(tall, "card")
    assert forced.getpixel((5, 5)) != arts.compose(tall, "bleed").getpixel((5, 5))
    assert arts.compose(wide, "card").size == (arts.WIDTH, arts.HEIGHT)


def test_bleed_does_not_distort_the_logo():
    """The block that holds the logo is only shifted and shrunk, never stretched."""
    out = arts.compose(_art(720, 1280), "bleed")
    dark = np.asarray(out.convert("L")) < 80
    rows = np.where(dark[:, 360:720].any(axis=1))[0]
    height = rows.max() - rows.min() + 1
    assert height == pytest.approx(0.1 * arts.HEIGHT * arts.BLEED_SCALE, abs=6)  # 10% of the art
    assert rows.min() > 150  # clear of Instagram's top bar


def test_tint_accepts_cream_and_hex():
    art = _art(960, 1280, (250, 248, 240))
    assert arts.parse_tint("#102030", art) == (16, 32, 48)
    assert arts.parse_tint("cream", art) == (250, 248, 240)
