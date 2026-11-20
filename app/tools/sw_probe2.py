import sys
import win32com.client
import pythoncom
import win32com.client

TEMPLATE = r"C:\Users\user\Documents\solidworks模板\A3横向默认.drwdot"


def t(name, fn):
    try:
        r = fn()
        print("OK  ", name, "=>", repr(r)[:200])
        return r
    except Exception as ex:
        print("FAIL", name, "=>", ex)
        return None


def main():
    sw = win32com.client.GetActiveObject("SldWorks.Application")
    print("SW version:", t("RevisionNumber", lambda: sw.RevisionNumber()))
    errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = t("NewDocument", lambda: sw.NewDocument(TEMPLATE, 0, 0, 0))
    if doc is None:
        return 1
    print("doc type:", type(doc))
    title = t("GetTitle", lambda: doc.GetTitle())
    adoc = t("ActiveDoc", lambda: sw.ActiveDoc)
    print("adoc type:", type(adoc))
    t("adoc.GetTitle", lambda: adoc.GetTitle())
    sheet = t("adoc.Sheet", lambda: adoc.Sheet)
    sheet2 = t("doc.Sheet", lambda: doc.Sheet)
    ext = t("doc.Extension", lambda: doc.Extension)
    cpm = t("adoc.Extension.CustomPropertyManager", lambda: adoc.Extension.CustomPropertyManager)
    if cpm:
        t("cpm.Add3", lambda: cpm.Add3("图号", 0, "03.OW.TEST-01"))
        t("cpm.Get", lambda: cpm.Get("图号"))
    pdf_data = t("CreateExportPDFData", lambda: adoc.Extension.CreateExportPDFData())
    if pdf_data is None:
        pdf_data = t("ExportPdfData (property)", lambda: adoc.Extension.ExportPdfData)
    pdf_path = r"C:\Users\user\Documents\Codex\2026-08-15\work\sw_test\probe.pdf"
    t("SaveAs3", lambda: doc.SaveAs3(pdf_path, 0, 1, pdf_data, errs, warns))
    import os
    print("pdf exists:", os.path.exists(pdf_path))
    try:
        doc.SetSaveFlag(False)
    except Exception:
        pass
    sw.CloseDoc(title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
