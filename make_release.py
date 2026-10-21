"""バリアブルフォントから、配布用の静的フォントを書き出す。

使い方: python3 make_release.py <VF.ttf> <出力ディレクトリ>
（skia-pathops が必要: pip install fonttools skia-pathops）

丸め版（OCR-A Axis）と角版（OCR-A Axis Sharp）を別のファミリーにし、
それぞれ 9 ウェイト × 立体／Oblique の 18 ファイルを作る。
アプリのスタイル切り替え（Regular / Bold / Italic）が正しく働くよう、
名前と OS/2・head の印を設定する。
"""
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.ttLib.removeOverlaps import removeOverlaps
from fontTools.varLib.instancer import instantiateVariableFont

WEIGHTS = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium",
           600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black"}
SLANT = -10
FAMILIES = {100: ("OCR-A Axis", "OCRAAxis"), 0: ("OCR-A Axis Sharp", "OCRAAxisSharp")}  # ROND → (ファミリー名, PostScript 名)


def set_names(font, family, ps_family, wname, oblique):
    style = wname if not oblique else ("Oblique" if wname == "Regular" else f"{wname} Oblique")
    ribbi = wname in ("Regular", "Bold")
    # 古いアプリ向けの名前（ファミリーごとに Regular / Bold / Italic / Bold Italic の4つまで）
    legacy_family = family if ribbi else f"{family} {wname}"
    legacy_style = {(False, False): "Regular", (True, False): "Bold",
                    (False, True): "Italic", (True, True): "Bold Italic"}[(wname == "Bold", oblique)]
    ps_name = f"{ps_family}-{style.replace(' ', '')}"
    name = font["name"]
    name.names = [r for r in name.names if r.nameID not in (1, 2, 3, 4, 6, 16, 17, 25)]
    for name_id, value in ((1, legacy_family), (2, legacy_style), (3, f"{ps_name};{font['OS/2'].achVendID}"),
                           (4, f"{family} {style}"), (6, ps_name), (16, family), (17, style)):
        name.setName(value, name_id, 3, 1, 0x409)
    return ps_name


def set_style_bits(font, wght, wname, oblique):
    os2 = font["OS/2"]
    os2.usWeightClass = wght
    sel = os2.fsSelection & ~((1 << 0) | (1 << 5) | (1 << 6) | (1 << 9))
    if oblique:
        sel |= (1 << 0) | (1 << 9)  # ITALIC, OBLIQUE
    if wname == "Bold":
        sel |= 1 << 5  # BOLD
    if wname != "Bold" and not oblique:
        sel |= 1 << 6  # REGULAR（古いアプリでの各ファミリーの標準スタイル）
    os2.fsSelection = sel
    font["head"].macStyle = (1 if wname == "Bold" else 0) | (2 if oblique else 0)
    font["post"].italicAngle = float(SLANT) if oblique else 0.0
    hhea = font["hhea"]
    hhea.caretSlopeRise = 1000
    hhea.caretSlopeRun = round(math.tan(math.radians(-SLANT)) * 1000) if oblique else 0


def build(job):
    src, out, wght, rond, oblique = job
    family, ps_family = FAMILIES[rond]
    wname = WEIGHTS[wght]
    font = instantiateVariableFont(TTFont(src), {"wght": wght, "ROND": rond, "slnt": SLANT if oblique else 0})
    removeOverlaps(font)
    for tag in ("STAT",):
        if tag in font:
            del font[tag]
    ps_name = set_names(font, family, ps_family, wname, oblique)
    set_style_bits(font, wght, wname, oblique)
    path = Path(out) / f"{ps_name}.ttf"
    font.save(path)
    return str(path)


def main(src, out):
    Path(out).mkdir(parents=True, exist_ok=True)
    jobs = [(src, out, wght, rond, oblique) for rond in FAMILIES for oblique in (False, True) for wght in WEIGHTS]
    with ProcessPoolExecutor() as pool:
        for path in pool.map(build, jobs):
            print("wrote", path)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
