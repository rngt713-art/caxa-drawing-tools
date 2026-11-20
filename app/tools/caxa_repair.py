"""修复孤立零件：把已存在但没有父链接的零件挂到对应类别下，使其在树中可见可删除。"""
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

import config  # noqa: E402
from caxa.bridge import CaxaBridge, CaxaError  # noqa: E402

# (零件图号, 父类别图号)
REPAIR_PAIRS = [
    ("04.13.MTEST-01", "04.13"),
    ("04.10.MTEST-01", "04.10"),
    ("01.70.MTEST-01", "01.70"),
]


def find_part(bridge, identity):
    r = bridge.call({"cmd": "query_parts", "keyword": identity})
    for it in r.get("items") or []:
        if (it.get("identity") or "").upper() == identity.upper():
            return it
    return None


def main():
    cfg = config.load()
    if not cfg.get("caxa_user") or not cfg.get("caxa_password_enc"):
        print("请先在主程序“设置”里保存 CAXA 账号密码")
        return 1
    bridge = CaxaBridge()
    try:
        bridge.login(cfg["caxa_server_ip"], int(cfg["caxa_server_port"]),
                     cfg["caxa_user"], config.get_password(cfg))
        print("登录成功：", cfg["caxa_user"])
        for code, parent in REPAIR_PAIRS:
            part = find_part(bridge, code)
            p = find_part(bridge, parent)
            if part is None:
                print("零件不存在，跳过：", code)
                continue
            if p is None:
                print("父类别不存在，跳过：", parent)
                continue
            print("修复：零件 %s（%s，id=%s）→ 类别 %s（id=%s）" % (
                code, part.get("name"), part.get("object_id"), parent, p.get("object_id")))
            try:
                r = bridge.link_part(p["object_id"], part["object_id"], reference=False)
                print("  已挂接：", r.get("link"))
            except CaxaError as ex:
                print("  挂接返回：", ex)
    except CaxaError as ex:
        print("CAXA 错误：", ex)
        return 1
    finally:
        bridge.stop()
    print("完成：请到 CAXA 客户端刷新，删除这几个测试零件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
