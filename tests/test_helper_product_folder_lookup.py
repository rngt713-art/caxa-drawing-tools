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


_PRODUCT_INDEX_HARNESS = r"""
using System;
using System.Collections.Generic;
using System.Linq;

class IEAPProductEntity
{
    public int ObjectID;
    public int ClassID;
    public string IdentityNo;
    public string Name;
    public IEAPPrd2PartEntity[] Parts;
    public IEAPPrd2PartEntity[] RelatedRootParts(IEAPPrd2PartEntity condition)
    {
        return Parts ?? new IEAPPrd2PartEntity[0];
    }
}

class IEAPPrd2PartEntity
{
    public IEAPProductEntity RefProduct;
    public IEAPProductEntity RefPart;
    public bool Reference;
}

class IEAPProductFolderEntity
{
    public string Name;
    public string IdentityNo;
    public bool ProductsFail;
    public bool ChildrenFail;
    public IEAPProductEntity[] Products;
    public IEAPProductFolderEntity[] SubFolders;
    public IEAPProductEntity[] RelatedProducts
    {
        get
        {
            if (ProductsFail)
                throw new InvalidOperationException("products unavailable");
            return Products;
        }
    }
    public IEAPProductFolderEntity[] Children
    {
        get
        {
            if (ChildrenFail)
                throw new InvalidOperationException("children unavailable");
            return SubFolders;
        }
    }
}

class MockService
{
    public bool HasLogin = true;
    public IEAPProductEntity[] Products;
    public IEAPProductFolderEntity[] Folders;
    public Dictionary<int, IEAPProductEntity> ProductsById =
        new Dictionary<int, IEAPProductEntity>();
    public IEAPProductEntity[] GetAllRootProducts() { return Products; }
    public IEAPProductFolderEntity[] GetAllRootProductFolders() { return Folders; }
    public IEAPProductEntity GetProduct(int id)
    {
        IEAPProductEntity product;
        return ProductsById.TryGetValue(id, out product) ? product : null;
    }
}

class ProductIndexTest
{
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

    static void Require(bool value, string message)
    {
        if (!value) throw new Exception(message);
    }

    // HELPERS

    static void Main()
    {
        try
        {
            Run();
        }
        catch (Exception ex)
        {
            Console.WriteLine(
                "TEST_FAIL " + ex.GetType().FullName + ": " + ex.Message);
            Environment.Exit(1);
        }
    }

    static void Run()
    {
        var nested = new IEAPProductEntity {
            ObjectID = 350, ClassID = 1012,
            IdentityNo = "03.HT.HGB05-05025", Name = "HGB 5.0",
            Parts = new [] {
                new IEAPPrd2PartEntity {
                    RefPart = new IEAPProductEntity {
                        ObjectID = 44135,
                        IdentityNo = "03.HT.HGB05-05025B",
                        Name = "HGB5-5/25"
                    }
                }
            }
        };
        var backup = new IEAPProductFolderEntity {
            Name = "备用目录",
            Products = new [] { nested }
        };
        var hgb = new IEAPProductFolderEntity {
            Name = "HGB系列",
            Products = new [] { nested }
        };
        var ht = new IEAPProductFolderEntity {
            Name = "HT合同特殊",
            SubFolders = new [] { hgb, backup }
        };
        var unreadable = new IEAPProductFolderEntity {
            Name = "无权限目录", ProductsFail = true
        };
        _svc.Products = new [] {
            new IEAPProductEntity {
                ObjectID = 1, ClassID = 1012,
                IdentityNo = "01", Name = "通用件"
            }
        };
        _svc.ProductsById[1] = _svc.Products[0];
        _svc.ProductsById[350] = nested;
        _svc.Folders = new [] { ht, unreadable };

        var result = CmdListProducts(new Dictionary<string, object> {
            { "include_root_parts", true }
        });
        Require((bool)result["ok"], "product index must return");
        Require(!(bool)result["complete"], "read error must make index partial");
        Require(((List<object>)result["errors"]).Count == 1, "record one subtree error");
        var products = (List<object>)result["products"];
        Require(products.Count == 2, "deduplicate repeated Product links");

        var root = (Dictionary<string, object>)products[0];
        Require(Convert.ToString(root["folder_path"]) == "", "root product has no folder");

        var row = (Dictionary<string, object>)products[1];
        Require((int)row["object_id"] == 350, "keep nested product id");
        Require(
            Convert.ToString(row["folder_path"]) == "HT合同特殊/HGB系列",
            "keep first actual folder path"
        );
        var paths = (List<object>)row["folder_paths"];
        Require(paths.Count == 2, "keep every actual ProductFolder path");
        Require(
            paths.Any(value =>
                Convert.ToString(value) == "HT合同特殊/备用目录"),
            "include secondary ProductFolder path"
        );
        var rootParts = (List<object>)row["root_parts"];
        Require(rootParts.Count == 1, "keep the Product root Part");
        Require(
            Convert.ToString(
                ((Dictionary<string, object>)rootParts[0])["identity"]
            ) == "03.HT.HGB05-05025B",
            "include the machine Part identity"
        );

        var targeted = CmdProductRootParts(new Dictionary<string, object> {
            { "product_ids", new [] { 350 } }
        });
        var targetedItems = (List<object>)targeted["items"];
        Require(targetedItems.Count == 1, "return one targeted Product");
        var targetedRow = (Dictionary<string, object>)targetedItems[0];
        Require(
            ((List<object>)targetedRow["root_parts"]).Count == 1,
            "targeted lookup returns the root Part"
        );
    }
}
"""


_SERIES_FOLDER_INDEX_HARNESS = r"""
using System;
using System.Collections.Generic;
using System.Linq;

class IEAPProductEntity
{
    public int ObjectID;
}

class IEAPProductFolderEntity
{
    public int ObjectID;
    public int ClassID;
    public string IdentityNo;
    public string Name;
    public IEAPProductFolderEntity[] SubFolders;
    public int ChildrenReads;
    public int ProductReads;
    public IEAPProductEntity[] RelatedProducts
    {
        get
        {
            ProductReads++;
            return new IEAPProductEntity[0];
        }
    }
    public IEAPProductFolderEntity[] Children
    {
        get
        {
            ChildrenReads++;
            return SubFolders ?? new IEAPProductFolderEntity[0];
        }
    }
}

class MockService
{
    public bool HasLogin = true;
    public IEAPProductFolderEntity[] Roots;
    public IEAPProductFolderEntity[] GetAllRootProductFolders() { return Roots; }
}

class SeriesFolderIndexTest
{
    static readonly MockService _svc = new MockService();
    static Dictionary<int, List<string>> _productFolderPathIndex;
    static List<object> _productFolderPathErrors;
    static List<object> _productFolderIndexRows;
    static List<object> _productFolderIndexErrors;

    static Dictionary<string, object> Ok(Dictionary<string, object> result)
    {
        result["ok"] = true;
        return result;
    }

    static Dictionary<string, object> Err(string message)
    {
        return new Dictionary<string, object> { { "ok", false }, { "error", message } };
    }

    static void Require(bool value, string message)
    {
        if (!value) throw new Exception(message);
    }

    // HELPERS

    static void Main()
    {
        var hgb = new IEAPProductFolderEntity {
            ObjectID = 8, ClassID = 1013, Name = "HGB系列"
        };
        var ht = new IEAPProductFolderEntity {
            ObjectID = 7, ClassID = 1013, Name = "HT合同特殊",
            SubFolders = new [] { hgb }
        };
        _svc.Roots = new [] {
            new IEAPProductFolderEntity {
                ObjectID = 1, ClassID = 1013, Name = "LSYZ两室一站"
            },
            ht,
            new IEAPProductFolderEntity {
                ObjectID = 2, ClassID = 1013, Name = "OW系列"
            },
            new IEAPProductFolderEntity {
                ObjectID = 3, ClassID = 1013, Name = "NS防爆箱"
            },
            new IEAPProductFolderEntity {
                ObjectID = 4, ClassID = 1013, Name = "SFC防爆箱"
            },
            new IEAPProductFolderEntity {
                ObjectID = 5, ClassID = 1013, Name = "SFD防爆箱"
            }
        };

        var first = CmdProductFolderIndex();
        var second = CmdProductFolderIndex();
        Require((bool)first["ok"], "folder index must return");
        Require((bool)first["complete"], "folder index must be complete");
        Require(ht.ChildrenReads == 1, "folder tree is read once and cached");
        Require(ht.ProductReads == 0, "series index must not scan Products");
        var rows = (List<object>)second["items"];
        Require(rows.Count == 7, "return every ProductFolder path");
        var nested = rows
            .Cast<Dictionary<string, object>>()
            .Single(row => Convert.ToString(row["path"]) == "HT合同特殊/HGB系列");
        var parts = (List<object>)nested["path_parts"];
        Require(parts.Count == 2, "preserve nested path parts");
        Require(
            Convert.ToString(parts[0]) == "HT合同特殊" &&
            Convert.ToString(parts[1]) == "HGB系列",
            "nested path parts stay independent of display separators"
        );
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
                capture_output=True, text=True, encoding="mbcs", errors="replace",
                check=True, timeout=30,
            )
            result = subprocess.run(
                [str(exe)], capture_output=True, text=True,
                encoding="mbcs", errors="replace", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_product_index_returns_every_actual_folder_path(self):
        helper = HELPER.read_text(encoding="utf-8")
        methods = "\n\n".join(
            _method(helper, signature) for signature in (
                "    static Dictionary<string, object> CmdListProducts(",
                "    static Dictionary<string, object> CmdProductRootParts(",
                "    static void AddProductRootParts(",
                "    static void CollectProductsWithPath(",
                "    static string NormalizeProductFolderPath(",
            )
        )
        with support.temp_dir(prefix="product_index_") as folder:
            source = Path(folder) / "ProductIndex.cs"
            exe = source.with_suffix(".exe")
            source.write_text(
                _PRODUCT_INDEX_HARNESS.replace("// HELPERS", methods),
                encoding="utf-8-sig",
            )
            subprocess.run(
                [CSC, "/nologo", "/platform:x86", "/out:" + str(exe), str(source)],
                capture_output=True, text=True, encoding="mbcs", errors="replace",
                check=True, timeout=30,
            )
            result = subprocess.run(
                [str(exe)], capture_output=True, text=True,
                encoding="mbcs", errors="replace", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_series_folder_index_is_cached_in_helper_session(self):
        helper = HELPER.read_text(encoding="utf-8")
        methods = "\n\n".join(
            _method(helper, signature) for signature in (
                "    static Dictionary<string, object> CmdProductFolderIndex(",
                "    static void EnsureSeriesFolderIndex(",
                "    static void IndexSeriesFolder(",
                "    static string NormalizeProductFolderPath(",
            )
        )
        with support.temp_dir(prefix="series_folder_index_") as folder:
            source = Path(folder) / "SeriesFolderIndex.cs"
            exe = source.with_suffix(".exe")
            source.write_text(
                _SERIES_FOLDER_INDEX_HARNESS.replace("// HELPERS", methods),
                encoding="utf-8-sig",
            )
            subprocess.run(
                [CSC, "/nologo", "/platform:x86", "/out:" + str(exe), str(source)],
                capture_output=True, text=True, encoding="mbcs", errors="replace",
                check=True, timeout=30,
            )
            result = subprocess.run(
                [str(exe)], capture_output=True, text=True,
                encoding="mbcs", errors="replace", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
