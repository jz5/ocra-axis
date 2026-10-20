% OCR-A の描画命令を差し替えて、中心線（スケルトン）をテキストに書き出す。
% msk-ocra.mp のマクロ定義の後、文字定義の前に読み込む。

string skelfile;
skelfile := "skeleton-round.txt";

def skel_pt(expr p) =
  decimal xpart p & " " & decimal ypart p
enddef;

def skel_path(expr kind, p) =
  write kind & " " & (if cycle p: "1" else: "0" fi) & " " & decimal length p to skelfile;
  for i = 0 upto length p - 1:
    write "  " & skel_pt(point i of p) & " " & skel_pt(postcontrol i of p) & " "
      & skel_pt(precontrol i + 1 of p) & " " & skel_pt(point i + 1 of p) to skelfile;
  endfor
enddef;

def ocrdraw(expr dp) = skel_path("S", dp); enddef;

def ocrdrawdot(expr pt) = write "D " & skel_pt(pt) to skelfile; enddef;

def dangerousFill text glist = skel_path("F", glist); enddef;

def fill_corners (text t) =
  forsuffixes $=t: write "C " & skel_pt(z$) to skelfile; endfor
enddef;

def fill_corner_clipped (suffix x)(expr y) =
  write "C " & skel_pt(z[x]) to skelfile;
enddef;

def angle_fill_corner (suffix a, b, c) =
  write "C " & skel_pt(z.b) to skelfile;
enddef;

let skel_orig_ocrchar = ocrchar;
def ocrchar (expr name, code) =
  write "G " & name & " " & decimal code & " " & decimal R & " " & decimal T
    & " " & skel_pt(((R-W)/2, T/2)) to skelfile;
  skel_orig_ocrchar(name, code)
enddef;

let skel_orig_endchar = endchar;
def endchar =
  write "E" to skelfile;
  skel_orig_endchar
enddef;
