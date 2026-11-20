import re
import sys

import win32com.client


def main():
    sw = win32com.client.GetActiveObject("SldWorks.Application")
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
                print("已关闭空白图：", title)
        except Exception:
            pass
    print("清理完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
