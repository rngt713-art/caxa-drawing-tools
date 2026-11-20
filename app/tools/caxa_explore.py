import json
import os
import sys
import time
import tkinter as tk
from tkinter import messagebox, simpledialog

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

import config  # noqa: E402
from caxa.bridge import CaxaBridge, CaxaError  # noqa: E402

OUTPUT = os.path.join(APP_DIR, "..", "..", "work", "caxa_tree.json")
OUTPUT = os.path.abspath(OUTPUT)
PRODUCTS_OUT = os.path.join(APP_DIR, "..", "..", "work", "caxa_products.json")
PRODUCTS_OUT = os.path.abspath(PRODUCTS_OUT)
PARTINFO_OUT = os.path.join(APP_DIR, "..", "..", "work", "part_info.json")
PARTINFO_OUT = os.path.abspath(PARTINFO_OUT)
PROGRESS = os.path.join(APP_DIR, "..", "..", "work", "caxa_progress.txt")
PROGRESS = os.path.abspath(PROGRESS)
TARGET_CANDIDATES = ["003250A-21P01", "氦气", "HE0.3-250S", "HE系列"]


def log(msg):
    line = time.strftime("%H:%M:%S") + " " + msg
    try:
        os.makedirs(os.path.dirname(PROGRESS), exist_ok=True)
        with open(PROGRESS, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def ask_credentials(cfg):
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    user = simpledialog.askstring("CAXA 登录", "CAXA 用户名：", initialvalue=cfg.get("caxa_user", ""), parent=root)
    if user is None:
        return None
    pwd = simpledialog.askstring("CAXA 登录", "CAXA 密码：", show="*", parent=root)
    if pwd is None:
        return None
    root.destroy()
    return user, pwd


def count_nodes(node, stats):
    kind = node.get("kind", "?")
    stats[kind] = stats.get(kind, 0) + 1
    for child in node.get("children", []) or []:
        count_nodes(child, stats)


def main():
    cfg = config.load()
    if "--reset" in sys.argv or not cfg.get("caxa_password_enc"):
        creds = ask_credentials(cfg)
        if creds is None:
            print("已取消")
            return 1
        user, pwd = creds
        config.set_credentials(user, pwd)
    else:
        user = cfg["caxa_user"]
        pwd = config.get_password(cfg)
        print("使用已保存的账号：" + user)

    bridge = CaxaBridge()
    try:
        try:
            os.remove(PROGRESS)
        except Exception:
            pass
        log("正在登录 CAXA ...")
        info = bridge.login(cfg["caxa_server_ip"], int(cfg["caxa_server_port"]), user, pwd)
        log("登录成功：user=" + str(info.get("user")) + " version=" + str(info.get("version")))
        log("正在获取产品列表 ...")
        products = bridge.call({"cmd": "list_products"})
        plist = products.get("products", [])
        log("产品总数：" + str(len(plist)))
        for item in plist:
            log("  产品 " + str(item.get("object_id")) + " | " + str(item.get("identity")) + " | " + str(item.get("name")))
        try:
            with open(PRODUCTS_OUT, "w", encoding="utf-8") as f:
                json.dump(plist, f, ensure_ascii=False, indent=1)
            log("产品列表已保存：" + PRODUCTS_OUT)
        except Exception as ex:
            log("产品列表保存失败：" + str(ex))
        tree = None
        for cand in TARGET_CANDIDATES:
            log("正在尝试导出：" + cand)
            try:
                tree = bridge.call({"cmd": "dump_product", "identity": cand, "max_depth": 6})
                log("匹配成功：" + cand)
                break
            except CaxaError as e:
                log("  未匹配：" + str(e))
        if tree is None:
            log("所有候选都未匹配，请从上面的产品列表中找出目标整机，我可以调整后再试。")
            return 1
        os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump(tree, f, ensure_ascii=False, indent=1)
        log("结构树已导出：" + OUTPUT)
        partinfo = {"part_infos": [], "find_results": {}}
        part_ids = []
        for child in tree.get("product", {}).get("children", []) or []:
            if child.get("kind") == "part":
                part_ids.append(child.get("object_id"))
            if len(part_ids) >= 2:
                break
        part_ids = list(dict.fromkeys(part_ids + [14369, 14363]))
        for pid in part_ids:
            try:
                partinfo["part_infos"].append(bridge.call({"cmd": "part_info", "part_id": pid}))
                log("零件信息已获取：" + str(pid))
            except CaxaError as e:
                log("零件信息失败 " + str(pid) + "：" + str(e))
        for kw in ["04.13.M3300-07", "M3300", "传动组件"]:
            try:
                r = bridge.call({"cmd": "query_parts", "keyword": kw})
                partinfo.setdefault("query_parts", {})[kw] = r
                log("查询零件 " + kw + "：count=" + str(r.get("count")))
            except CaxaError as e:
                log("查询零件失败 " + kw + "：" + str(e))
        with open(PARTINFO_OUT, "w", encoding="utf-8") as f:
            json.dump(partinfo, f, ensure_ascii=False, indent=1)
        log("零件信息已保存：" + PARTINFO_OUT)
    except CaxaError as e:
        log("CAXA 错误：" + str(e))
        return 1
    finally:
        bridge.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
