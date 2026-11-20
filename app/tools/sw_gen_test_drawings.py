"""用 SolidWorks 生成“只有标题栏”的测试图纸 PDF，用于 CAXA 上传联调。"""
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.abspath(os.path.join(APP_DIR, "..", "..", "work", "sw_test"))
TEMPLATE = r"C:\Users\user\Documents\solidworks模板\A3横向默认.drwdot"

SPECS = [
    # (文件名, 图号, 名称, 成品代码/整机代码)
    ("03.OW.TEST-01 测试增压机", "03.OW.TEST-01", "测试增压机", "03.OW.TEST-01"),
    ("J03.OW.TEST-01", "J03.OW.TEST-01", "测试检测工艺", "03.OW.TEST-01"),
    ("Z03.OW.TEST-01", "Z03.OW.TEST-01", "测试总装图", "03.OW.TEST-01"),
    ("M03.OW.TEST-01", "M03.OW.TEST-01", "测试铭牌", "03.OW.TEST-01"),
    ("04.13.MTEST-01 传动组件", "04.13.MTEST-01", "传动组件", "03.OW.TEST-01"),
    ("04.10.MTEST-01 进气组件", "04.10.MTEST-01", "进气组件", "03.OW.TEST-01"),
    ("01.70.MTEST-01 法兰", "01.70.MTEST-01", "法兰", "03.OW.TEST-01"),
]


def resolve(obj, name, *args):
    """win32com 属性/方法兼容调用：能调用就调用，否则按属性取。"""
    attr = getattr(obj, name)
    try:
        return attr(*args)
    except TypeError:
        return attr


def cleanup_leftovers(sw):
    import re
    docs = sw.GetDocuments()
    for d in docs:
        try:
            path = d.GetPathName()
            title = d.GetTitle()
            if not path and re.match(r"^工程图\d+", title or ""):
                try:
                    d.SetSaveFlag(False)
                except Exception:
                    pass
                sw.CloseDoc(title)
                print("已关闭残留空白图：", title)
        except Exception:
            pass


def main():
    import win32com.client
    import pythoncom

    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        sw = win32com.client.GetActiveObject("SldWorks.Application")
        print("已连接运行中的 SolidWorks")
    except Exception:
        print("未找到运行中的 SolidWorks，尝试启动…")
        sw = win32com.client.Dispatch("SldWorks.Application")
    sw.Visible = True
    cleanup_leftovers(sw)

    results = []
    for fname, code, name, machine in SPECS:
        try:
            doc = sw.NewDocument(TEMPLATE, 0, 0, 0)
            if not doc:
                results.append((fname, "FAIL NewDocument 返回空"))
                print("FAIL:", fname, "- NewDocument 返回空")
                continue
            title = resolve(doc, "GetTitle")
            sheet = None
            try:
                sheet = resolve(doc, "Sheet")
            except Exception as ex:
                print("  doc.Sheet 不可用：", ex)
            if sheet is None:
                try:
                    sheet = resolve(doc, "GetActiveSheet")
                except Exception as ex:
                    print("  GetActiveSheet 不可用：", ex)
            cpm = None
            try:
                cpm = resolve(sheet, "CustomPropertyManager")
            except Exception as ex:
                print("  sheet.CustomPropertyManager 不可用：", ex)
            if cpm is None:
                try:
                    cpm = resolve(doc.Extension, "CustomPropertyManager")
                except Exception as ex:
                    print("  doc.Extension.CustomPropertyManager 不可用：", ex)
            if cpm is not None:
                for prop, val in (("图号", code), ("名称", name), ("成品代码", machine)):
                    if not val:
                        continue
                    try:
                        cpm.Add3(prop, 0, val)
                    except Exception:
                        try:
                            cpm.Add2(prop, val)
                        except Exception as ex2:
                            print("  写入属性 %s 失败：%s" % (prop, ex2))
                print("  已写入属性：图号=%s 名称=%s 成品代码=%s" % (code, name, machine))
            else:
                print("  !!! 无法写入自定义属性")
            pdf_path = os.path.join(OUT_DIR, fname + ".pdf")
            pdf_data = resolve(doc.Extension, "CreateExportPDFData")
            errs = pythoncom.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            warns = pythoncom.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            ok = False
            try:
                ok = doc.SaveAs3(pdf_path, 0, 1, pdf_data, errs, warns)
            except Exception as ex:
                print("  SaveAs3 异常：", ex)
                ok = resolve(doc, "SaveAs3", pdf_path, 0, 1, pdf_data, errs, warns)
            if ok:
                results.append((fname, "OK"))
                print("OK:", fname)
            else:
                results.append((fname, "FAIL SaveAs3 err=%s warn=%s" % (errs.value, warns.value)))
                print("FAIL:", fname, "SaveAs3 err=%s warn=%s" % (errs.value, warns.value))
            try:
                doc.SetSaveFlag(False)
            except Exception:
                pass
            sw.CloseDoc(title)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            results.append((fname, "EXC " + str(ex)))
            print("EXC:", fname, ex)
    print("\n输出目录：", OUT_DIR)
    for r in results:
        print(r)
    print("\n当前 SW 打开的文档：")
    try:
        docs = sw.GetDocuments()
        for i in range(docs.GetCount()):
            d = docs.Item(i + 1)
            print(" -", d.GetTitle(), "| path:", d.GetPathName())
    except Exception as ex:
        print(" 枚举失败：", ex)
    return 0


if __name__ == "__main__":
    sys.exit(main())
