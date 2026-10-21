"""バリアブルフォントから、README 用の見本画像（preview.png）を描く。

使い方: .venv/bin/python -I make_preview.py dist/OCRAAxis-VF.ttf preview.png
（Pillow が必要。requirements.txt を .venv に入れておく。ふだんは build.sh から呼ぶ）

太さ・角の丸み・傾き・代替字形・全文字を1枚に並べる。
3つの軸の値は、Pillow に直接渡さず、fontTools の instancer で静的フォントに切り出してから使う
（Pillow に3軸を渡すと異常終了することがあるため）。
"""
import argparse
import io
import sys
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I で実行しても fontinfo を読めるように
import fontinfo  # noqa: E402
from fontinfo import SLANT  # noqa: E402

WIDTH = 1600
MARGIN = 80
BG = (246, 245, 241)
INK = (27, 27, 26)
MUTED = (125, 122, 115)
LINE = (221, 218, 211)
ACCENT = (194, 65, 12)

WEIGHTS = list(fontinfo.WEIGHTS.items())
# 代替字形（cmap にない）を描くため、切り出したフォントで私用領域の文字に割り当てる
ALTS = [("ss01", ".", "period.alt", "period"), ("ss02", "?", "question.alt", "question"),
        ("ss03", "'", "quotesingle.alt", "apostrophe"), ("ss04", ",", "comma.alt", "comma"),
        ("ss05", "-", "hyphen.alt", "hyphen")]
ALT_BASE = 0xE000
CHARSET = ["0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZ",
           "abcdefghijklmnopqrstuvwxyz",
           "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~",
           "£¥ÄÅÆÑÖØÜ⑀⑁⑂│■"]

VF_PATH = None


@lru_cache(maxsize=None)
def font_data(wght, rond, slnt):
    vf = TTFont(VF_PATH)
    font = instantiateVariableFont(vf, {"wght": wght, "ROND": rond, "slnt": slnt})
    for table in font["cmap"].tables:
        if table.isUnicode():
            for i, (_, _, glyph, _) in enumerate(ALTS):
                table.cmap[ALT_BASE + i] = glyph
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


@lru_cache(maxsize=None)
def font(size, wght=400, rond=100, slnt=0):
    return ImageFont.truetype(io.BytesIO(font_data(wght, rond, slnt)), size)


class Page:
    def __init__(self, height):
        self.image = Image.new("RGB", (WIDTH, height), BG)
        self.draw = ImageDraw.Draw(self.image)
        self.y = MARGIN

    def text(self, x, y, s, f, fill=INK):
        self.draw.text((x, y), s, font=f, fill=fill, anchor="ls")

    def section(self, title, note):
        """区切り線と、見出し（軸の名前と範囲）。"""
        self.y += 56
        self.draw.line((MARGIN, self.y, WIDTH - MARGIN, self.y), fill=LINE, width=2)
        self.y += 52
        self.text(MARGIN, self.y, title, font(28, 700), ACCENT)
        self.text(MARGIN + font(28, 700).getlength(title) + 24, self.y, note, font(24, 400), MUTED)
        self.y += 24


def render(page):
    # 題名と軸の一覧
    page.y += 100
    page.text(MARGIN, page.y, fontinfo.FAMILY, font(128, 700))
    page.y += 64
    page.text(MARGIN, page.y, "A variable OCR-A rebuilt from the ANSI X3.17 centerlines", font(30, 300), MUTED)
    page.y += 46
    page.text(MARGIN, page.y, "wght 100-900   ROND 0-100   slnt 0 to -10", font(30, 300), MUTED)

    # 太さ：2つのファミリーを左右に
    page.section("WEIGHT", "wght 100-900, two families x 9 weights")
    col = (WIDTH - 2 * MARGIN) // 2
    page.y += 56
    for i, name in enumerate(f"{fontinfo.FAMILIES[r][0]}  (ROND {r})" for r in (100, 0)):
        page.text(MARGIN + i * col, page.y, name, font(24, 500), MUTED)
    page.y += 18
    for wght, name in WEIGHTS:
        page.y += 68
        for i, rond in enumerate((100, 0)):
            x = MARGIN + i * col
            page.text(x, page.y, f"{name}", font(22, 400), MUTED)
            page.text(x, page.y + 22, f"{wght}", font(18, 400), MUTED)
            page.text(x + 200, page.y + 6, "Aa@$& 0123", font(54, wght, rond))

    # 角の丸み：同じ字を ROND の5段階で
    page.section("ROUNDNESS", "ROND 100 (original OCR-A) to 0 (square corners)")
    steps = (100, 75, 50, 25, 0)
    cell = (WIDTH - 2 * MARGIN) // len(steps)
    page.y += 160
    for i, rond in enumerate(steps):
        x = MARGIN + i * cell
        page.text(x, page.y, "B@", font(150, 700, rond))
        page.text(x, page.y + 50, f"ROND {rond}", font(24, 400), MUTED)
    page.y += 50

    # 傾き
    page.section("SLANT", "slnt 0 to -10, every weight has an Oblique")
    for label, rond, slnt in (("Oblique", 100, SLANT), ("Sharp Oblique", 0, SLANT)):
        page.y += 92
        page.text(MARGIN, page.y, label, font(22, 400), MUTED)
        page.text(MARGIN + 240, page.y, "Oblique 0123", font(64, 300, rond, slnt))
        page.text(MARGIN + 240 + font(64).getlength("Oblique 0123 "), page.y, "Oblique",
                  font(64, 800, rond, slnt))

    # 代替字形：標準と代替を並べる
    page.section("ALTERNATES", "ss01-ss05, from the original OCR-A")
    cell = (WIDTH - 2 * MARGIN) // len(ALTS)
    page.y += 130
    big = font(96, 500)
    for i, (tag, ch, _, name) in enumerate(ALTS):
        x = MARGIN + i * cell
        page.text(x, page.y, ch, big)
        page.text(x + 72, page.y - 10, "/", font(56, 100), MUTED)
        page.text(x + 116, page.y, chr(ALT_BASE + i), big)
        page.text(x, page.y + 50, f"{tag} {name}", font(21, 400), MUTED)
    page.y += 50

    # 全文字
    page.section("CHARACTER SET", "ASCII, Latin-1 subset and OCR-A specials")
    for line in CHARSET:
        page.y += 74
        page.text(MARGIN, page.y, line, font(52, 400))
    page.y += MARGIN


def main():
    parser = argparse.ArgumentParser(description="バリアブルフォントから、README 用の見本画像を描く。")
    parser.add_argument("vf", help="バリアブルフォント（OCRAAxis-VF.ttf）")
    parser.add_argument("out", help="出力する PNG（preview.png）")
    args = parser.parse_args()
    global VF_PATH
    VF_PATH = args.vf
    out = args.out
    # 高さを測るために一度描き、その高さで描き直す
    probe = Page(4000)
    render(probe)
    page = Page(probe.y)
    render(page)
    page.image.save(out, optimize=True)
    print("wrote", out, page.image.size)


if __name__ == "__main__":
    main()
