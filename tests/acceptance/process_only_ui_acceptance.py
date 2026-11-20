"""Real Tk regression for batches containing only 02/03 process documents."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parent
APP = (
    Path(sys.argv.pop(1)).resolve()
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
    else ROOT / "dsh_fix" / "app"
)
sys.path[:0] = [str(ROOT / "test-deps"), str(APP)]

import upload_main as ui


class ProcessOnlyUIAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.patches = [
            patch.object(ui.config, "load", return_value={}),
            patch.object(ui.config, "save"),
            patch.object(ui.messagebox, "showwarning"),
            patch.object(ui.messagebox, "showinfo"),
            patch.object(ui.messagebox, "showerror"),
        ]
        for item in self.patches:
            item.start()
        self.app = ui.UploadApp()
        self.app.withdraw()
        self.app.update_idletasks()

    def tearDown(self):
        self.app.destroy()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_only_03_process_documents_form_product_group_and_share_name(self):
        app = self.app
        paths = []
        for code in (
            "J03.HW.025400-11T01",
            "M03.HW.025400-11T01",
            "Z03.HW.025400-11T01",
        ):
            path = Path(self.tmp.name) / ("%s.pdf" % code)
            path.write_bytes(("PDF-" + code).encode("ascii"))
            paths.append(str(path))
        app.add_pdfs(paths)

        machine = "03.HW.025400-11T01"
        product_iid = next(
            iid
            for iid, code in app._tree_product_nodes.items()
            if code == machine
        )
        self.assertEqual(
            app._tree_product_groups[app.tree.parent(product_iid)],
            "HW系列",
        )
        document_iids = [
            iid
            for iid, item in app._tree_items.items()
            if item is not None
            and str(item.get("kind") or "") == "process_doc"
            and item.get("machine") == machine
        ]
        self.assertEqual(len(document_iids), 3)
        self.assertTrue(all(
            app.tree.parent(iid) == product_iid for iid in document_iids
        ))

        app.tree.selection_set(document_iids[0])
        app._on_tree_selection_changed()
        self.assertEqual(app._series_product_code, machine)
        app.series_product_var.set("HW2.5-40")
        app._on_series_product_name_changed()
        self.assertTrue(all(
            item.get("series_product_name") == "HW2.5-40"
            for item in app.items
            if item.get("machine") == machine
        ))

    def test_02_process_document_remains_supported_without_03_product_plan(self):
        app = self.app
        path = Path(self.tmp.name) / "Z02.A36.02.ARF25C.pdf"
        path.write_bytes(b"PDF-Z02")
        app.add_pdfs([str(path)])
        item = next(
            item for item in app.items
            if item.get("code") == "Z02.A36.02.ARF25C"
        )
        self.assertEqual(item.get("kind"), "process_doc")
        self.assertEqual(item.get("machine"), "02.A36.02.ARF25C")
        self.assertEqual(app._tree_product_nodes, {})
        self.assertNotIn("series_product_name", item)


if __name__ == "__main__":
    unittest.main()
