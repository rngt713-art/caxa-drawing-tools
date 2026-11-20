"""Exercise the real C# upload boundary with an offline core that injects faults.

Only CmdUpload is compiled, so no CAXA assemblies, COM, or server are involved.
"""

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


@unittest.skipUnless(os.name == "nt" and os.path.isfile(CSC), "Windows C# compiler required")
class HelperUploadContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = _support.temp_dir("helper_context_")
        cls.root = cls.temp.__enter__()
        source = Path(_support.APP_DIR, "caxa", "caxa_helper", "caxa_helper.cs").read_text(
            encoding="utf-8"
        )
        begin = source.index("    static Dictionary<string, object> CmdUpload(")
        end = source.index("    static Dictionary<string, object> CmdUploadCore(", begin)
        method = source[begin:end]
        harness = """
using System;
using System.Collections.Generic;
class Harness {
    static volatile bool _uploadInProgress = false;
    static volatile int _uploadCurrentObjectId = 0;
    static Dictionary<string, object> Err(string error) {
        return new Dictionary<string, object> { { "ok", false }, { "error", error } };
    }
    static Dictionary<string, object> CmdUploadCore(Dictionary<string, object> a) {
        if (!_uploadInProgress) throw new Exception("upload scope missing");
        if (a.ContainsKey("object_id"))
            _uploadCurrentObjectId = Convert.ToInt32(a["object_id"]);
        if (a.ContainsKey("fail")) throw new Exception("injected failure");
        return new Dictionary<string, object> { { "ok", true } };
    }
""" + method + """
    static int Main(string[] args) {
        // First PDF succeeds in this same long-lived helper process.
        CmdUpload(new Dictionary<string, object> { { "object_id", 101 } });
        var next = new Dictionary<string, object> { { "fail", true } };
        if (args[0] == "known") next["object_id"] = 202;
        var result = CmdUpload(next);
        if (_uploadInProgress || (bool)result["ok"] || !(bool)result["uncertain"])
            return 1;
        if (args[0] == "known")
            return result.ContainsKey("object_id") && (int)result["object_id"] == 202 ? 0 : 2;
        return result.ContainsKey("object_id") ? 3 : 0;
    }
}
"""
        cs_path = Path(cls.root, "UploadContext.cs")
        cs_path.write_text(harness, encoding="utf-8")
        cls.exe = str(Path(cls.root, "UploadContext.exe"))
        compiled = subprocess.run(
            [CSC, "/nologo", "/platform:x86", "/out:" + cls.exe, str(cs_path)],
            capture_output=True, text=True,
        )
        if compiled.returncode:
            cls.temp.__exit__(None, None, None)
            raise AssertionError(compiled.stdout + compiled.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.__exit__(None, None, None)

    def test_early_failure_does_not_inherit_previous_pdf_object(self):
        result = subprocess.run([self.exe, "unknown"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_partial_failure_keeps_current_pdf_evidence_and_clears_scope(self):
        result = subprocess.run([self.exe, "known"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
