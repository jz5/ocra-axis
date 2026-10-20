"""バリアブルフォントから静的フォントを1つ切り出し、重なりを除去する。

使い方: python3 make_static.py <VF.ttf> <wght> <ROND> <スタイル名> <出力.ttf>
（skia-pathops が必要: pip install fonttools skia-pathops）
"""
import sys

from fontTools.ttLib import TTFont
from fontTools.ttLib.removeOverlaps import removeOverlaps
from fontTools.varLib.instancer import instantiateVariableFont


def main(src, wght, rond, style, dst):
    font = instantiateVariableFont(TTFont(src), {"wght": float(wght), "ROND": float(rond)})
    removeOverlaps(font)
    name = font["name"]
    family = name.getDebugName(1)
    ps_name = f"{family}-{style}".replace(" ", "")
    for name_id, value in ((2, style), (4, f"{family} {style}"), (6, ps_name), (3, ps_name)):
        name.setName(value, name_id, 3, 1, 0x409)
        name.setName(value, name_id, 1, 0, 0)
    font["OS/2"].usWeightClass = int(wght)
    font.save(dst)
    print("wrote", dst)


if __name__ == "__main__":
    main(*sys.argv[1:6])
