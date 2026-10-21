"""バリアブルフォントから、配布用の静的フォントを書き出す。

使い方: .venv/bin/python -I make_release.py <VF.ttf> <出力ディレクトリ>
（重なりの除去に skia-pathops が要る。requirements.txt を .venv に入れておく。ふだんは build.sh から呼ぶ）

丸め版（OCR-A Axis）と角版（OCR-A Axis Sharp）を別のファミリーにし、
それぞれ 9 ウェイト × 立体／Oblique の 18 ファイルを作る。
アプリのスタイル切り替え（Regular / Bold / Italic）が正しく働くよう、
名前と OS/2・head の印を設定する。
"""
import argparse
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.ttLib.removeOverlaps import removeOverlaps
from fontTools.varLib.instancer import instantiateVariableFont

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I で実行しても fontinfo を読めるように
import fontinfo  # noqa: E402
from fontinfo import FAMILIES, SLANT, WEIGHTS  # noqa: E402


def set_names(font, wght, rond, oblique):
    family = FAMILIES[rond][0]
    wname = WEIGHTS[wght]
    style = fontinfo.static_style(wght, oblique)
    ribbi = wname in ("Regular", "Bold")
    # 古いアプリ向けの名前（ファミリーごとに Regular / Bold / Italic / Bold Italic の4つまで）
    legacy_family = family if ribbi else f"{family} {wname}"
    legacy_style = {(False, False): "Regular", (True, False): "Bold",
                    (False, True): "Italic", (True, True): "Bold Italic"}[(wname == "Bold", oblique)]
    ps = fontinfo.ps_name(wght, rond, oblique)
    name = font["name"]
    name.names = [r for r in name.names if r.nameID not in (1, 2, 3, 4, 6, 16, 17, 25)]
    for name_id, value in ((1, legacy_family), (2, legacy_style), (3, fontinfo.unique_id(ps)),
                           (4, f"{family} {style}"), (6, ps), (16, family), (17, style)):
        name.setName(value, name_id, 3, 1, 0x409)
    return ps


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
    font = instantiateVariableFont(TTFont(src), {"wght": wght, "ROND": rond, "slnt": SLANT if oblique else 0})
    removeOverlaps(font)
    # 静的フォントには軸がないので、軸ごとのスタイル名の表（STAT）は要らない
    del font["STAT"]
    ps = set_names(font, wght, rond, oblique)
    set_style_bits(font, wght, WEIGHTS[wght], oblique)
    # STAT や fvar のために作った名前（ID 256 以降で、どこからも使われなくなったもの）を消す
    font["name"].removeUnusedNames(font)
    path = Path(out) / f"{ps}.ttf"
    font.save(path)
    return str(path)


def main():
    parser = argparse.ArgumentParser(description="バリアブルフォントから、配布用の静的フォント 36 個を書き出す。")
    parser.add_argument("vf", help="バリアブルフォント（OCRAAxis-VF.ttf）")
    parser.add_argument("outdir", help="出力ディレクトリ")
    args = parser.parse_args()
    Path(args.outdir).mkdir(parents=True, exist_ok=True)
    jobs = [(args.vf, args.outdir, wght, rond, oblique)
            for rond in FAMILIES for oblique in (False, True) for wght in WEIGHTS]
    with ProcessPoolExecutor() as pool:
        for path in pool.map(build, jobs):
            print("wrote", path)


if __name__ == "__main__":
    main()
