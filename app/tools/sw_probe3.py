import sys
import win32com.client

TEMPLATE = r"C:\Users\user\Documents\solidworks模板\A3横向默认.drwdot"


def main():
    sw = win32com.client.GetActiveObject("SldWorks.Application")
    doc = sw.NewDocument(TEMPLATE, 0, 0, 0)
    sheet = doc.Sheet()
    print("sheet type:", type(sheet))
    try:
        cpm = doc.Extension.CustomPropertyManager()
        print("cpm type:", type(cpm))
        print("Add3:", cpm.Add3("图号", 0, "03.OW.TEST-01"))
        print("Get:", cpm.Get("图号"))
        print("Add3 名称:", cpm.Add3("名称", 0, "测试增压机"))
        print("Add3 成品代码:", cpm.Add3("成品代码", 0, "03.OW.TEST-01"))
    except Exception as ex:
        print("cpm fail:", ex)
    ext = doc.Extension
    print("== dir(ext) ==")
    names = [n for n in dir(ext) if not n.startswith("_")]
    print(len(names))
    for n in names:
        if any(k in n.lower() for k in ("pdf", "export", "save", "custom")):
            print("  ", n)
    # 尝试多种 PDF 导出名
    for cand in ("CreateExportPDFData", "CreateExportPdfData", "ExportPDF", "ExportPdf", "CreateExportPdfData"):
        try:
            v = getattr(ext, cand)
            print("found:", cand, type(v))
            if callable(v):
                r = v()
                print("  called =>", r)
        except Exception as ex:
            print("no:", cand, str(ex)[:80])
    import os
    pdf_path = r"C:\Users\user\Documents\Codex\2026-08-15\work\sw_test\probe2.pdf"
    try:
        os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
        out = doc.SaveAs3(pdf_path, 0, 0, 0, 0, 0)
        print("SaveAs3 plain:", out, "exists:", os.path.exists(pdf_path))
    except Exception as ex:
        print("SaveAs3 fail:", ex)
    try:
        doc.SetSaveFlag(False)
    except Exception:
        pass
    sw.CloseDoc(doc.GetTitle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
