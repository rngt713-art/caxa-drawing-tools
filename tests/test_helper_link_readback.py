"""Compile the real helper link routines against a small offline CAXA double."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import _support


CSC = shutil.which("csc") or os.path.join(
    os.environ.get("WINDIR", r"C:\Windows"),
    "Microsoft.NET", "Framework", "v4.0.30319", "csc.exe",
)


FAKE_CAXA = r"""
using System;
using System.Collections.Generic;
using CAXA.A5.EAP.CommonLibrary.Session.ObjectStore;

namespace CAXA.A5.EAP.CommonLibrary {
    class EAPLibEngine {
        public static EAPLibEngine Instance = new EAPLibEngine();
        public static FakeStore Store { get { return Instance.CurSession.ObjectStore; } }
        public FakeSession CurSession = new FakeSession();
    }
    class FakeSession {
        public FakeStore ObjectStore = new FakeStore();
        public MetaInfo.FakeMetaInfo MetaInfo = new MetaInfo.FakeMetaInfo();
    }
}
namespace CAXA.A5.EAP.CommonLibrary.MetaInfo {
    class ICaxClassBase { public short ClassId = 1040; }
    class FakeMetaInfo {
        public ICaxClassBase GetLinkClass(int left, int right) { return new ICaxClassBase(); }
    }
}
namespace CAXA.A5.EAP.CommonLibrary.Session.ObjectStore {
    class ICaxObject { public int ObjectId; }
    class ICaxLinkObject : ICaxObject {
        public ICaxObject RoleAObject, RoleBObject;
        public void SetValue(string name, object value) {
            if (name == "Reference" && CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Store.ReferenceSetFails)
                throw new Exception("injected Reference assignment failure");
        }
        public bool Insert() {
            FakeStore store = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Store;
            store.Inserts++;
            return store.InsertResult;
        }
    }
    class FakeStore {
        public int Inserts;
        public bool InsertResult = true;
        public bool ReferenceSetFails;
        public Dictionary<int, ICaxObject> Objects = new Dictionary<int, ICaxObject>();
        public ICaxObject RetrieveObject(short cls, int id) {
            return Objects.ContainsKey(id) ? Objects[id] : null;
        }
        public ICaxLinkObject NewOneLevelLink(short cls, ICaxObject parent, ICaxObject child, object data) {
            return new ICaxLinkObject { ObjectId = 99, RoleAObject = parent, RoleBObject = child };
        }
    }
}
class IEAPPart2PartEntity {
    public short ClassID = 1037;
    public int ObjectID = 98;
    public bool Reference;
    public int AssemblyQuantity;
    public IEAPPartEntity RefSubPart;
    public bool Deleted;
    public void Delete() { Deleted = true; }
}
class IEAPPartEntity : ICaxObject {
    public int ObjectID { get { return ObjectId; } }
    public int HighCalls;
    public bool RefreshFails;
    public List<IEAPPart2PartEntity> Links = new List<IEAPPart2PartEntity>();
    public Func<IEAPPart2PartEntity> OnLink;
    public void Refresh() { if (RefreshFails) throw new Exception("injected refresh failure"); }
    public IEAPPart2PartEntity[] Children(object ignored) { return Links.FindAll(x => !x.Deleted).ToArray(); }
    public IEAPPart2PartEntity LinkSubPart(IEAPPartEntity child, IEAPPart2PartEntity condition) {
        HighCalls++;
        return OnLink == null ? null : OnLink();
    }
}
class IEAPPrd2PartEntity {
    public short ClassID = 1040;
    public int ObjectID = 97;
    public bool Reference;
    public IEAPPartEntity RefPart;
    public void Delete() { }
}
class IEAPProductEntity : ICaxObject {
    public int ObjectID { get { return ObjectId; } }
    public bool RefreshFails;
    public IEAPPrd2PartEntity[] Related = new IEAPPrd2PartEntity[0];
    public void Refresh() { if (RefreshFails) throw new Exception("injected product refresh failure"); }
    public IEAPPrd2PartEntity[] RelatedRootParts(IEAPPrd2PartEntity condition) { return Related; }
    public IEAPPrd2PartEntity LinkRelatedPart(IEAPPartEntity part, IEAPPrd2PartEntity condition) {
        return null;
    }
    public bool UnLinkPart(IEAPPartEntity part, IEAPPrd2PartEntity condition) {
        RefreshFails = true;
        return true;
    }
}
class IEAPProductFolderEntity {
    public int ObjectID = 88;
    public IEAPProductEntity[] RelatedProducts;
    public bool RefreshFails;
    public void Refresh() { if (RefreshFails) throw new Exception("injected folder refresh failure"); }
    public bool UnLinkProduct(IEAPProductEntity product) { RefreshFails = true; return true; }
}
class FakeService {
    public bool HasLogin = true;
    public IEAPPartEntity Parent = new IEAPPartEntity { ObjectId = 1 };
    public IEAPPartEntity Child = new IEAPPartEntity { ObjectId = 2 };
    public IEAPProductEntity Product = new IEAPProductEntity { ObjectId = 3 };
    public IEAPProductFolderEntity Folder = new IEAPProductFolderEntity();
    public IEAPPartEntity GetPart(int id) { return id == 1 ? Parent : Child; }
    public IEAPProductEntity GetProduct(int id) { return Product; }
    public IEAPPart2PartEntity NewPart2Part() { return new IEAPPart2PartEntity(); }
    public IEAPPrd2PartEntity NewPrd2Part() { return new IEAPPrd2PartEntity(); }
}
class Harness {
    static FakeService _svc = new FakeService();
    static List<IEAPProductEntity> GetAllProductsList() {
        return new List<IEAPProductEntity> { _svc.Product };
    }
    static IEAPProductFolderEntity FindProductFolderRecursive(string folder, out string path) {
        path = folder;
        return _svc.Folder;
    }
    static bool _deleteDialogAutoConfirm;
"""


DRIVER = r"""
    static void Check(bool value, string message) {
        if (!value) throw new Exception(message);
    }
    static int Main(string[] args) {
        FakeStore store = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Store;
        store.Objects[1] = _svc.Parent;
        store.Objects[2] = _svc.Child;
        store.Objects[3] = _svc.Product;
        var request = new Dictionary<string, object> {
            { "parent_id", 1 }, { "child_id", 2 }, { "product_id", 3 }, { "part_id", 2 },
            { "product_folder", "series" }, { "link_id", 98 }, { "link_class_id", 1038 }
        };
        switch (args[0]) {
            case "null-after-write":
            case "exception-after-write":
                _svc.Parent.OnLink = () => {
                    // This client's COM relationship does not expose RefSubPart.
                    _svc.Parent.Links.Add(new IEAPPart2PartEntity());
                    store.Objects[98] = new ICaxLinkObject {
                        RoleAObject = _svc.Parent, RoleBObject = _svc.Child
                    };
                    if (args[0] == "exception-after-write") throw new Exception("after write");
                    return null;
                };
                Check((int)CmdLinkPart(request)["link_id"] == 98, "did not recover existing relationship");
                Check(store.Inserts == 0, "duplicate low-level insert");
                break;
            case "unknown-after-write":
            case "unresolved-endpoint":
                _svc.Parent.OnLink = () => {
                    if (args[0] == "unknown-after-write") _svc.Parent.RefreshFails = true;
                    else _svc.Parent.Links.Add(new IEAPPart2PartEntity());
                    return null;
                };
                Check((bool)CmdLinkPart(request)["uncertain"], "unverifiable absence not reported");
                Check(store.Inserts == 0, "low-level insert with no absence evidence");
                break;
            case "confirmed-absence":
                store.ReferenceSetFails = true; // Default False preserves ownership.
                Check((bool)CmdLinkPart(request)["ok"], "compatibility fallback failed");
                Check(_svc.Parent.HighCalls == 1 && store.Inserts == 1, "fallback path was lost");
                break;
            case "borrow":
                request["reference"] = true;
                Check((bool)CmdLinkPart(request)["ok"], "borrow insert failed");
                Check(_svc.Parent.HighCalls == 0 && store.Inserts == 1, "borrow went through high-level link");
                break;
            case "borrow-reference-failed":
                request["reference"] = true;
                store.ReferenceSetFails = true;
                Check(!(bool)CmdLinkPart(request)["ok"], "untyped borrow falsely reported success");
                Check(_svc.Parent.HighCalls == 0 && store.Inserts == 0, "borrow inserted as ownership");
                break;
            case "product-insert-false":
                request["parent_id"] = 3;
                request["product_link"] = true;
                store.InsertResult = false;
                Check((bool)CmdLinkPart(request)["uncertain"], "Insert(false) reported success");
                Check(store.Inserts == 1, "product fallback not attempted");
                break;
            case "unlink-folder-readback":
                _svc.Folder.RelatedProducts = new[] { _svc.Product };
                Check((bool)CmdUnlinkProductFromFolder(request)["uncertain"], "folder unlink falsely confirmed");
                break;
            case "unlink-product-part-readback":
                _svc.Product.Related = new[] { new IEAPPrd2PartEntity { RefPart = _svc.Child } };
                Check((bool)CmdUnlinkProductPart(request)["uncertain"], "product-part unlink falsely confirmed");
                break;
            case "unlink-wrong-class":
                var link = new IEAPPart2PartEntity { RefSubPart = _svc.Child };
                _svc.Parent.Links.Add(link);
                Check(!(bool)CmdUnlinkPartRelation(request)["ok"] && !link.Deleted, "removed the wrong class");
                break;
            default:
                return 1;
        }
        return 0;
    }
}
"""


@unittest.skipUnless(os.name == "nt" and os.path.isfile(CSC), "Windows C# compiler required")
class HelperLinkReadbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = cls.enterClassContext(_support.temp_dir("helper_link_readback_"))
        source = Path(_support.APP_DIR, "caxa", "caxa_helper", "caxa_helper.cs").read_text(
            encoding="utf-8"
        )
        ok_and_error = source[source.index("    static Dictionary<string, object> Ok("):
                              source.index("    static Dictionary<string, object> AttrDict(")]
        unlink_begin = source.index("    static bool FolderHasProduct(")
        unlink_marker = source.index('{ "deleted_relation_only", true }', unlink_begin)
        unlink_end = source.index("    static ", unlink_marker)
        link_begin = source.index("    static Dictionary<string, object> CmdLinkPart(")
        link_end = source.index("    // CAXA's document import path", link_begin)
        cs = Path(cls.root, "LinkReadback.cs")
        cs.write_text(
            FAKE_CAXA + ok_and_error + source[unlink_begin:unlink_end] + source[link_begin:link_end] + DRIVER,
            encoding="utf-8",
        )
        cls.exe = str(Path(cls.root, "LinkReadback.exe"))
        result = subprocess.run(
            [CSC, "/nologo", "/platform:x86", "/out:" + cls.exe, str(cs)],
            capture_output=True, text=True, encoding="mbcs", errors="replace",
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def test_ownership_fallback_requires_confirmed_absence(self):
        self.check_cases(
            "null-after-write", "exception-after-write", "unknown-after-write",
            "unresolved-endpoint", "confirmed-absence",
        )

    def test_borrow_keeps_independent_low_level_write(self):
        self.check_cases("borrow", "borrow-reference-failed")

    def test_product_part_insert_failure_is_not_success(self):
        self.check_cases("product-insert-false")

    def test_hidden_unlink_requires_readback_and_exact_relation_identity(self):
        self.check_cases("unlink-folder-readback", "unlink-product-part-readback", "unlink-wrong-class")

    def check_cases(self, *cases):
        for case in cases:
            with self.subTest(case=case):
                result = subprocess.run(
                    [self.exe, case], capture_output=True, text=True,
                    encoding="mbcs", errors="replace",
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
