#!/bin/sh
# フォントと見本画像をすべて作り直し、GitHub Releases に上げる zip を作る。
# 使い方: ./build.sh
# （.venv に requirements.txt を入れておく: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt）
#
# 書き出すもの:
#   dist/OCRAAxis-VF.ttf          バリアブルフォント
#   dist/static/*.ttf             静的フォント 36 個
#   dist/OCRAAxis-<版>.zip        上の2つと LICENSE・README.md・preview.png をまとめたもの
#   preview.png                   README 用の見本画像
set -e
cd "$(dirname "$0")"
PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  echo ".venv がありません。python3 -m venv .venv && .venv/bin/pip install -r requirements.txt を実行してください" >&2
  exit 1
fi
# フォントの作成日時を、最後のコミットの日時に固定する（同じ中身なら同じバイナリになる）
if [ -z "$SOURCE_DATE_EPOCH" ]; then
  SOURCE_DATE_EPOCH=$(git log -1 --format=%ct 2>/dev/null || date +%s)
fi
export SOURCE_DATE_EPOCH
VERSION=$("$PY" -I -c 'import sys; sys.path.insert(0, "."); import fontinfo; print(fontinfo.VERSION)')
NAME="OCRAAxis-$VERSION"

rm -rf dist/static "dist/$NAME" "dist/$NAME.zip"
"$PY" -I build_ocra.py skeleton-round.txt dist
"$PY" -I make_release.py dist/OCRAAxis-VF.ttf dist/static
"$PY" -I make_preview.py dist/OCRAAxis-VF.ttf preview.png

mkdir -p "dist/$NAME/static"
cp dist/OCRAAxis-VF.ttf LICENSE README.md preview.png "dist/$NAME/"
cp dist/static/*.ttf "dist/$NAME/static/"
(cd dist && zip -qrX "$NAME.zip" "$NAME")
rm -rf "dist/$NAME"
echo "wrote dist/$NAME.zip"
