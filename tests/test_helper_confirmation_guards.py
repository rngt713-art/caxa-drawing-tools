"""Run the helper's confirmation and linking guards with offline doubles."""

from pathlib import Path
import subprocess
import unittest

import _support
from test_helper_upload_context import CSC


@unittest.skipUnless(Path(CSC).is_file(), "Windows C# compiler required")
class HelperConfirmationGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = _support.temp_dir("helper_guards_")
        root = cls.enterClassContext(directory)
        cls.exe = str(Path(root, "HelperGuards.exe"))
        source = Path(
            _support.APP_DIR, "caxa", "caxa_helper", "caxa_helper.cs"
        ).read_text(encoding="utf-8")
        confirmation_start = source.index("                    int uploadSequence =")
        confirmation_end = source.index("                    // 2)", confirmation_start)
        linking_start = source.index('            stage = "link_documents";')
        linking_end = source.index("            // A successful start call", linking_start)
        source_file = Path(root, "HelperGuards.cs")
        source_file.write_text(
            HARNESS.replace("__CONFIRMATION__", source[confirmation_start:confirmation_end])
            .replace("__LINKING__", source[linking_start:linking_end]),
            encoding="utf-8",
        )
        result = subprocess.run(
            [CSC, "/nologo", "/platform:x86", "/out:" + cls.exe, str(source_file)],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def test_repeated_window_always_verifies_before_click(self):
        result = subprocess.run([self.exe, "dialog"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_incomplete_links_stop_workflow_with_document_evidence(self):
        for outcome in ("false", "null", "success"):
            with self.subTest(outcome=outcome):
                result = subprocess.run([self.exe, outcome], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


HARNESS = """
using System;
using System.Collections.Generic;
using CAXA.A5.EAP.CommonLibrary.Session.ObjectStore;

namespace CAXA.A5.EAP.CommonLibrary.Session.ObjectStore {
    class ICaxObject { public int ObjectId; }
}

class Harness {
    static int _uploadSequence = 1;
    static bool _autoEnabled = true;
    static bool nextSelection;
    static int selectionChecks, checkboxes, clicks;
    static void ConfirmLog(string message) { }
    static string SafeWindowTitle(IntPtr window) { return "duplicate"; }
    static bool IsAutoTargetAllowed(uint pid) { return _autoEnabled && pid == 1; }
    static IntPtr FindDupDialog(out string text, out uint pid) {
        text = "dup";
        pid = 1;
        return new IntPtr(77);
    }
    static bool SelectOptionAndVerify(IntPtr window, string label) {
        selectionChecks++;
        return nextSelection;
    }
    static bool SetCheckBoxChecked(IntPtr window, string label) {
        checkboxes++;
        return true;
    }
    static bool ClickButton(IntPtr window, string label) {
        clicks++;
        return true;
    }

    static int CheckDialog() {
        int lastUploadSequence = -1;
        var lastDup = IntPtr.Zero;
        bool dupCheckboxHandled = false;
        bool beat = false;
        for (int ticks = 0; ticks < 5; ticks++) {
            nextSelection = ticks >= 2 && ticks != 3;
            if (ticks == 4) _uploadSequence++;
            __CONFIRMATION__
        }
        return selectionChecks == 5 && clicks == 2 && checkboxes == 2 ? 0 : 1;
    }

    class Link {
        public int ObjectId;
        public bool Accepted;
        public bool Insert() { return Accepted; }
    }

    class Store {
        readonly string outcome;
        public Store(string outcome) { this.outcome = outcome; }
        public ICaxObject RetrieveObject(short cls, int id) {
            return new ICaxObject { ObjectId = id };
        }
        public Link NewOneLevelLink(short cls, ICaxObject parent, ICaxObject child, object data) {
            if (child.ObjectId == 22 && outcome == "null") return null;
            return new Link {
                ObjectId = child.ObjectId + 100,
                Accepted = child.ObjectId != 22 || outcome == "success"
            };
        }
    }

    class Process {
        public bool Started, PerformerDesignated;
        public void designate_dynamic_performer(uint id, string def, string activity, int version, string performer) {
            PerformerDesignated = true;
        }
        public void start(uint id) { Started = true; }
    }

    static Dictionary<string, object> CheckLinks(string outcome, Process process) {
        var result = new Dictionary<string, object>();
        var store = new Store(outcome);
        var processObject = new ICaxObject();
        uint processId = 100, performerId = 66;
        string stage = "", processDefId = "2", activityDefId = "4";
        var uniqueDrawingIds = new List<int> { 11, 22 };
        var classIdByDocumentId = new Dictionary<int, int> { { 11, 1002 }, { 22, 1003 } };
        Func<string, Dictionary<string, object>> fail = message => {
            result["error"] = message;
            result["stage"] = stage;
            return result;
        };
        __LINKING__
        return result;
    }

    static int Main(string[] args) {
        if (args[0] == "dialog") return CheckDialog();
        var process = new Process();
        var result = CheckLinks(args[0], process);
        var links = (List<int>)result["document_link_ids"];
        if (args[0] == "success") {
            return process.Started && process.PerformerDesignated && links.Count == 2
                && !result.ContainsKey("failed_drawing_id") ? 0 : 2;
        }
        return !process.Started && !process.PerformerDesignated
            && links.Count == 1 && links[0] == 111
            && (string)result["stage"] == "link_documents"
            && (int)result["failed_drawing_id"] == 22
            && (int)result["failed_document_class_id"] == 1003 ? 0 : 3;
    }
}
"""
