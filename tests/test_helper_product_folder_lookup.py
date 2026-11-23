"""Exercise the actual scoped C# lookup against an offline SDK-shaped tree."""

from pathlib import Path
import os
import shutil
import subprocess
import unittest

import _support as support


HELPER = Path(__file__).resolve().parents[1] / "app" / "caxa" / "caxa_helper" / "caxa_helper.cs"
CSC = shutil.which("csc") or os.path.join(
    os.environ.get("WINDIR", r"C:\Windows"), "Microsoft.NET", "Framework", "v4.0.30319", "csc.exe",
)


def _method(source, signature):
    first = source.index(signature)
    closing = source.index("\n    }", first) + len("\n    }")
    return source[first:closing]


_HARNESS = r"""
using System;
using System.Collections.Generic;
using System.Linq;

class IEAPProductEntity
{
    public int ObjectID;
    public string IdentityNo;
    public string Name;
}

class IEAPProductFolderEntity
{
    public string Name;
    public string IdentityNo;
    public bool ProductsFail;
    public bool ChildrenFail;
    public int ProductReads;
    public IEAPProductEntity[] Products;
    public IEAPProductFolderEntity[] SubFolders;
    public IEAPProductEntity[] RelatedProducts
    {
        get
        {
            ProductReads++;
            if (ProductsFail) throw new InvalidOperationException("products unavailable");
            return Products;
        }
    }
    public IEAPProductFolderEntity[] Children
    {
        get
        {
            if (ChildrenFail) throw new InvalidOperationException("children unavailable");
            return SubFolders;
        }
    }
}

class MockService
{
    public bool HasLogin = true;
    public IEAPProductFolderEntity[] Roots;
    public IEAPProductFolderEntity[] GetAllRootProductFolders() { return Roots; }
}

class LookupTest
{
    const string Code = "03.HT.HGB01-06350G";
    const string Target = "HT合同特殊/HGB系列";
    static readonly MockService _svc = new MockService();

    static Dictionary<string, object> Ok(Dictionary<string, object> result)
    {
        result["ok"] = true;
        return result;
    }

    static Dictionary<string, object> Err(string message)
    {
        return new Dictionary<string, object> { { "ok", false }, { "error", message } };
    }

    // HELPERS

    static void Require(bool value, string message)
    {
        if (!value) throw new Exception(message);
    }

    static Dictionary<string, object> Lookup(string identity)
    {
        return CmdFindProductInFolder(new Dictionary<string, object> {
            { "identity", identity }, { "folder_path", Target }
        });
    }

    static void Main()
    {
        var outside = new IEAPProductFolderEntity {
            Name = "其他系列", ProductsFail = true, ChildrenFail = true
        };
        var unreadable = new IEAPProductFolderEntity {
            Name = "读取失败的子目录", ProductsFail = true
        };
        var exact = new IEAPProductFolderEntity {
            Name = "压缩机组",
            Products = new [] { new IEAPProductEntity { ObjectID = 350, IdentityNo = Code } }
        };
        var late = new IEAPProductFolderEntity { Name = "无需继续扫描" };
        var target = new IEAPProductFolderEntity {
            Name = "HGB系列", SubFolders = new [] { unreadable, exact, late }
        };
        var root = new IEAPProductFolderEntity {
            Name = "HT合同特殊", SubFolders = new [] { target }
        };
        _svc.Roots = new [] { outside, root };

        var positive = Lookup(Code.ToLowerInvariant());
        Require((bool)positive["ok"], "a positive hit must survive an earlier read error");
        var matches = (List<object>)positive["found"];
        Require(matches.Count == 1, "return the first exact product");
        Require((int)((Dictionary<string, object>)matches[0])["object_id"] == 350, "match by exact identity");
        Require(late.ProductReads == 0, "stop scanning at the first exact hit");
        Require(outside.ProductReads == 0, "only read products within the target subtree");

        var unknown = Lookup("03.HT.HGB01-06350");
        Require(!(bool)unknown["ok"], "partial read must not report an authoritative miss");
        Require((string)unknown["stage"] == "product_folder_read", "classify failure as a read error");

        unreadable.ProductsFail = false;
        var missing = Lookup("03.HT.HGB01-06350");
        Require((bool)missing["ok"], "complete exact miss may report missing");
        Require(((List<object>)missing["found"]).Count == 0, "partial identity is not an exact hit");

        root.ChildrenFail = true;
        var unavailable = Lookup(Code);
        Require(!(bool)unavailable["ok"], "unreadable target folder path is not absence");
    }
}
"""


@unittest.skipUnless(os.name == "nt" and os.path.isfile(CSC), "Windows C# compiler required")
class ProductFolderLookupTests(unittest.TestCase):
    def test_positive_read_versus_incomplete_or_confirmed_absence(self):
        helper = HELPER.read_text(encoding="utf-8")
        methods = "\n\n".join(
            _method(helper, signature) for signature in (
                "    static IEAPProductFolderEntity FindScopedProductFolder(",
                "    static Dictionary<string, object> FindProductInFolderRecursive(",
                "    static string NormalizeProductFolderPath(",
                "    static Dictionary<string, object> CmdFindProductInFolder(",
            )
        )
        with support.temp_dir(prefix="product_lookup_") as folder:
            source = Path(folder) / "ProductLookup.cs"
            exe = source.with_suffix(".exe")
            source.write_text(_HARNESS.replace("// HELPERS", methods), encoding="utf-8-sig")
            subprocess.run(
                [CSC, "/nologo", "/platform:x86", "/out:" + str(exe), str(source)],
                capture_output=True, text=True, check=True, timeout=30,
            )
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
