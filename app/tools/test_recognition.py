import json
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

from pipeline import extract, pdf_tools, vision  # noqa: E402

OUT = os.path.abspath(os.path.join(APP_DIR, "..", "..", "work", "test_recognition.json"))
PAGES = [
    (r"D:\03.HE.003250A-21P01 氦气压缩机\检测\J03.HE.003250A-21P01.pdf", "J03检测报告", 0),
    (r"D:\03.HE.003250A-21P01 氦气压缩机\检测\J03.HE.003250A-21P01.pdf", "J03检测报告", 1),
    (r"D:\03.HE.003250A-21P01 氦气压缩机\总装\Z03.HE.003250A-21P01 氦气压缩机.PDF", "Z03总装图", 0),
    (r"D:\03.HE.003250A-21P01 氦气压缩机\GA\03.HE.003250A-21P01 氦气压缩机.PDF", "GA整机图", 0),
]


def main():
    v = vision.LocalVision()
    tmp = os.path.join(os.path.dirname(OUT), "recog_test")
    os.makedirs(tmp, exist_ok=True)
    results = []
    for path, label, idx in PAGES:
        if not os.path.exists(path):
            print("missing:", path)
            continue
        png = os.path.join(tmp, label.replace("/", "_") + "_p%d.png" % (idx + 1))
        pdf_tools.render_pdf_page(path, idx, png)
        raw = v.ask(png, extract.PROMPT)
        fields = extract.parse_vision_output(raw)
        resolved = extract.resolve_document(fields, source_filename=path)
        entry = {
            "label": label,
            "page": idx + 1,
            "raw": raw,
            "fields": fields,
            "resolved": resolved,
        }
        results.append(entry)
        print(json.dumps({"label": label, "page": idx + 1, "resolved": resolved}, ensure_ascii=False))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("saved:", OUT)


if __name__ == "__main__":
    main()
