"""直接生成“只有标题栏”的测试图纸 PDF（仿 GB 图框布局），用于 CAXA 上传联调。"""
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.abspath(os.path.join(APP_DIR, "..", "..", "work", "sw_test"))

SPECS = [
    ("03.OW.TEST-01 测试增压机", "03.OW.TEST-01", "测试增压机", "03.OW.TEST-01", "", []),
    ("J03.OW.TEST-01", "J03.OW.TEST-01", "测试检测工艺", "03.OW.TEST-01", "", []),
    ("Z03.OW.TEST-01", "Z03.OW.TEST-01", "测试总装图", "03.OW.TEST-01", "",
     [("1", "04.13.MTEST-01", "传动组件"), ("2", "04.10.MTEST-01", "进气组件")]),
    ("M03.OW.TEST-01", "M03.OW.TEST-01", "测试铭牌", "03.OW.TEST-01", "", []),
    ("04.13.MTEST-01 传动组件", "04.13.MTEST-01", "传动组件", "03.OW.TEST-01", "04.13", []),
    ("04.10.MTEST-01 进气组件", "04.10.MTEST-01", "进气组件", "03.OW.TEST-01", "04.10", []),
    ("01.70.MTEST-01 法兰", "01.70.MTEST-01", "法兰", "03.OW.TEST-01", "01.70", []),
]


def find_font():
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
              r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"):
        if os.path.exists(p):
            return p
    return None


def make_pdf(path, code, name, machine, parent, bom):
    from PIL import Image, ImageDraw, ImageFont

    W, H = 2480, 1752  # A3 横向 @150dpi
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    font = find_font()
    font_code = None
    for p in (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\msyh.ttc"):
        if os.path.exists(p):
            font_code = ImageFont.truetype(p, 64)
            break
    if font_code is None:
        font_code = ImageFont.truetype(font, 64)
    f_small = ImageFont.truetype(font, 34)
    f_mid = ImageFont.truetype(font, 46)
    f_big = ImageFont.truetype(font, 58)

    def cell(x0, y0, x1, y1, text="", font=f_mid, label=None):
        d.rectangle([x0, y0, x1, y1], outline="black", width=4)
        if label:
            d.text((x0 + 12, y0 + 8), label, fill="black", font=f_small)
            tx = x0 + 12
            ty = y0 + y1 - y0 - 60
        else:
            tx = x0 + 12
            ty = y0 + (y1 - y0 - 60) // 2
        d.text((tx, ty), text, fill="black", font=font)

    # 标题栏区域：右下角
    bx0, by0, bx1, by1 = W - 950, H - 620, W - 60, H - 60
    d.rectangle([bx0, by0, bx1, by1], outline="black", width=5)
    d.text((bx0 + 20, by0 + 20), "汉纬尔机械（上海）有限公司", fill="black", font=f_big)
    cell(bx0, by0 + 120, bx0 + 500, by0 + 200, label="图号", text=code, font=font_code)
    cell(bx0 + 500, by0 + 120, bx1, by0 + 200, label="名称", text=name, font=f_big)
    cell(bx0, by0 + 200, bx0 + 500, by0 + 280, label="成品代码", text=machine or "", font=font_code)
    cell(bx0 + 500, by0 + 200, bx1, by0 + 280, label="上级图号", text=parent or "", font=font_code)
    # 签字行
    labels = ["设计", "校对", "工艺", "标准化", "审核", "批准"]
    cw = (bx1 - bx0) // len(labels)
    for i, lab in enumerate(labels):
        cell(bx0 + i * cw, by0 + 280, bx0 + (i + 1) * cw, by0 + 360, label=lab)
    cell(bx0, by0 + 360, bx0 + (bx1 - bx0) // 2, by0 + 440, label="重量")
    cell(bx0 + (bx1 - bx0) // 2, by0 + 360, bx1, by0 + 440, label="比例")
    cell(bx0, by0 + 440, bx0 + (bx1 - bx0) // 2, by0 + 520, label="共1张")
    cell(bx0 + (bx1 - bx0) // 2, by0 + 440, bx1, by0 + 520, label="第1张")
            cell(bx0, by0 + 520, bx1, by0 + 560, label="版本 A  发布  2026-08-15  历史作者", font=f_small)
    # 材料明细表（可选）：画在标题栏上方
    if bom:
        b2x0, b2y0, b2x1, b2y1 = bx0, by0 - 40 - 90 * len(bom), bx1, by0 - 40
        d.rectangle([b2x0, b2y0, b2x1, b2y1], outline="black", width=5)
        cell(b2x0, b2y0, b2x0 + 120, b2y0 + 90, label="序号", font=f_small)
        cell(b2x0 + 120, b2y0, b2x0 + 620, b2y0 + 90, label="物料代码", font=f_small)
        cell(b2x0 + 620, b2y0, b2x1, b2y0 + 90, label="名称", font=f_small)
        for i, (no, c, nm) in enumerate(bom):
            yy = b2y0 + 90 + 90 * i
            cell(b2x0, yy, b2x0 + 120, yy + 90, text=no, font=f_small)
            cell(b2x0 + 120, yy, b2x0 + 620, yy + 90, text=c, font=font_code)
            cell(b2x0 + 620, yy, b2x1, yy + 90, text=nm, font=f_small)
    img.save(path, "PDF", resolution=150)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    if find_font() is None:
        print("未找到中文字体，无法生成")
        return 1
    for fname, code, name, machine, parent, bom in SPECS:
        p = os.path.join(OUT_DIR, fname + ".pdf")
        make_pdf(p, code, name, machine, parent, bom)
        print("生成：", p)
    print("输出目录：", OUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
