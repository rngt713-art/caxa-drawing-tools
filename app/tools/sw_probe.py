import sys

import win32com.client


def main():
    sw = win32com.client.GetActiveObject("SldWorks.Application")
    docs = sw.GetDocuments
    print("open docs:", len(docs))
    for d in docs:
        print(" -", repr(d.GetTitle), "| path:", repr(d.GetPathName))


if __name__ == "__main__":
    sys.exit(main())
