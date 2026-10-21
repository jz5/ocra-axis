"""OCR-A Axis の版・名前・著作権表示。

build_ocra.py・make_release.py・make_preview.py で共通に使う。
specimen.html にも同じ太さとファミリーの一覧があるので、変えるときはそちらも直す。
"""
VERSION = "1.000"  # 形や名前を変えて配るたびに上げる
URL = "https://github.com/jz5/ocra-axis"
VENDOR_ID = "prnm"  # OS/2 のベンダー ID（4 文字）
MANUFACTURER = "jz5"
DESIGNER = "Tor Lillqvist, Richard B. Wales, Matthew Skala; OCR-A Axis by jz5"

WEIGHTS = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium",
           600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black"}
SLANT = -10  # 斜めの軸（slnt）の最大。度で、負の値が右に傾く
# 配布する静的フォントのファミリー。ROND の値 → (ファミリー名, PostScript 名の前半)
FAMILIES = {100: ("OCR-A Axis", "OCRAAxis"), 0: ("OCR-A Axis Sharp", "OCRAAxisSharp")}
FAMILY, PS_FAMILY = FAMILIES[100]  # バリアブルフォントのファミリー

# Wales 氏の条件は「著作権表示とクレジットを intact に残す」なので、原文のまま入れる（LICENSE と同じ）
COPYRIGHT = (
    "METAFONT-84 Definition for OCR-A Optical Character Recognition Font "
    "(C) Copyright 1988, 1989 Richard B. Wales.  All Rights Reserved. "
    "Based on an earlier version by Tor Lillqvist. "
    "MetaType1 translation by Matthew Skala (public domain). "
    "OCR-A Axis, a stroke-based variable version derived from the Tsukurimashou OCR package, by jz5."
)
LICENSE_DESCRIPTION = (
    "Terms of copyright: You may do anything you wish with this font definition, as long as you retain "
    "the original copyright notice and credit information intact in the source, and do not charge any fee "
    "for distribution of either the source or any fonts derived from it beyond a reasonable copying or "
    "communications charge. "
    "OCR-A Axis is distributed under the same terms, without any warranty. See the LICENSE file for details."
)
LICENSE_URL = f"{URL}/blob/main/LICENSE"
DESCRIPTION = "OCR-A rebuilt from its ANSI X3.17 centerlines, with weight, corner roundness and slant axes."


def static_style(wght, oblique):
    """静的フォントのスタイル名（例: Regular, Oblique, Bold Oblique）。"""
    wname = WEIGHTS[wght]
    if not oblique:
        return wname
    return "Oblique" if wname == "Regular" else f"{wname} Oblique"


def ps_name(wght, rond, oblique):
    """静的フォントと、バリアブルフォントの名前付きスタイルに共通の PostScript 名（例: OCRAAxisSharp-BoldOblique）。"""
    return f"{FAMILIES[rond][1]}-{static_style(wght, oblique).replace(' ', '')}"


def unique_id(ps):
    return f"{VERSION};{VENDOR_ID};{ps}"

