"""Recognition/bundle boundary regressions; no PDF engine or service required."""
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

import _support
import drawing_bundle
from pipeline import extract


class RecognitionRulesTests(unittest.TestCase):
    def test_process_02_and_03_use_uploader_classification_and_filename(self):
        for code in ("Z02.A36.02.ARF25C", "J02.A36.02.ARF25C", "Z03.HE.001", "M03.HE.001"):
            with self.subTest(code=code):
                expected = drawing_bundle.infer_kind(code)
                self.assertEqual(extract.classify(code), (*expected, True))
                for fields, filename in (({"图号": code}, "scan.pdf"), ({"文件编号": code}, "scan.pdf"), ({}, code + ".pdf")):
                    actual = extract.resolve_document(fields, source_filename=filename)
                    self.assertEqual((actual["kind"], actual["machine"], actual["code"]), (*expected, code))
                    self.assertTrue(actual["ok"])
                self.assertEqual(extract.expected_file_name(code, "总装图"), code + ".pdf")

    def test_multi_letter_prefix_stays_rejected(self):
        self.assertEqual(extract.classify("ZJ03.HE.001"), ("unknown", "", False))
        self.assertFalse(extract.resolve_document({"图号": "ZJ03.HE.001"})["ok"])

    def test_process_bundle_round_trip_preserves_display_name_not_filename(self):
        for code in ("Z02.A36.02.ARF25C", "J03.HE.001"):
            with self.subTest(code=code), _support.temp_dir() as td:
                pdf, _, _ = _support.write_pdf(td, "scan.pdf")
                item = extract.resolve_document({"图号": code, "名称": "检测报告"})
                item["page_pdf"] = pdf
                manifest_path = drawing_bundle.export_bundle([item], Path(td) / "out")
                _, loaded = drawing_bundle.load_bundle(manifest_path)
                actual = loaded["items"][0]
                self.assertEqual(actual["name"], "")
                self.assertEqual(actual["display_name"], "检测报告")
                self.assertEqual(Path(actual["file"]).name, code + ".pdf")
                self.assertEqual(item["name"], "检测报告")

    def test_invalid_export_fails_before_writing_output(self):
        with _support.temp_dir() as td:
            pdf, _, _ = _support.write_pdf(td, "scan.pdf")
            invalid = {"code": "ZJ03.HE.001", "name": "", "kind": "process_doc", "machine": "03.HE.001", "page_pdf": pdf}
            out = Path(td) / "out"
            with self.assertRaisesRegex(ValueError, "上传包校验失败"):
                drawing_bundle.export_bundle([invalid], out)
            self.assertFalse(out.exists())


class IsolatedPipelineTests(unittest.TestCase):
    """Import production modules against injected PDF/image I/O adapters only."""
    def setUp(self):
        self.package = types.ModuleType("offline_pipeline")
        self.package.__path__ = [str(Path(_support.APP_DIR) / "pipeline")]
        self.pdf = types.ModuleType("offline_pipeline.pdf_tools")
        self.pdf.extract_all_text = Mock(return_value=["物料代码 01.A.1"])
        self.pdf.find_codes_in_text = Mock(return_value=["01.A.1"])
        self.pdf.split_pdf = Mock(side_effect=lambda src, out: [src + "_page.pdf"])
        self.pdf.render_pdf_page = Mock()
        self.vision = types.ModuleType("offline_pipeline.vision")
        self.pil = types.ModuleType("PIL")
        self.pil.Image = Mock()
        self.modules = patch.dict(sys.modules, {"offline_pipeline": self.package, "offline_pipeline.extract": extract, "offline_pipeline.pdf_tools": self.pdf, "offline_pipeline.vision": self.vision, "PIL": self.pil})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def load(self, name):
        filename = Path(_support.APP_DIR) / "pipeline" / (name + ".py")
        spec = importlib.util.spec_from_file_location("offline_pipeline." + name, filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        setattr(self.package, name, module)
        return module

    def test_assembly_path_normalization_and_filename_priority(self):
        matcher = self.load("matcher")
        code = "03.NA.050080-S01"
        for filename in ("Z" + code + ".pdf", "z" + code.lower() + ".PDF", os.path.join("C:\\drawings", "Z" + code + ".pdf")):
            with self.subTest(filename=filename):
                self.assertEqual(matcher.assembly_machine_from_filename(filename), code)
                self.assertEqual(matcher.load_assembly(filename)["machine"], code)
        self.assertEqual(matcher.assembly_machine_from_filename("ZJ03.NA.1.pdf"), "")

    def test_recognition_splits_each_source_once_and_keeps_progress(self):
        self.load("matcher")
        core = self.load("recognize_core")
        v = Mock()
        # An ordinary machine drawing that needs no image retry or BOM I/O.
        v.ask.return_value = "图号: 03.HE.001\n名称: 氦气压缩机\n成品代码: 03.HE.001"
        progress = Mock()
        with _support.temp_dir() as td:
            scans = ["a.pdf", "b.pdf", "c.pdf"]
            items, _, warnings = core.run_recognition(scans, "", [], v, work_dir=td, log=lambda *args: None, progress=progress)
        self.assertEqual(self.pdf.split_pdf.call_count, len(scans))
        self.assertEqual(self.pdf.render_pdf_page.call_count, len(scans))
        self.assertEqual(v.ask.call_count, len(scans))
        self.assertEqual([call.args for call in progress.call_args_list], [(0, 3), (1, 3), (2, 3)])
        self.assertEqual(len(items), 3)
        self.assertEqual(warnings, [])


class VisionReadinessTests(unittest.TestCase):
    def setUp(self):
        self.http = types.ModuleType("requests")
        self.http.get = Mock(return_value=types.SimpleNamespace(status_code=200))
        self.reply = Mock()
        self.reply.json.return_value = {"choices": [{"message": {"content": "字段"}}]}
        self.http.post = Mock(return_value=self.reply)
        filename = Path(_support.APP_DIR) / "pipeline" / "vision.py"
        spec = importlib.util.spec_from_file_location("offline_vision", filename)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"requests": self.http}):
            spec.loader.exec_module(self.module)
        self.now = 100.0
        self.module.time = types.SimpleNamespace(monotonic=lambda: self.now)
        self.v = self.module.LocalVision(server="http://remote.invalid:8081")

    def test_repeated_requests_share_readiness_until_ttl_expires(self):
        with _support.temp_dir() as td:
            image, _, _ = _support.write_pdf(td, "page.png", b"fake image transport")
            for _ in range(4):
                self.assertEqual(self.v.ask(image, "转录"), "字段")
            self.assertEqual(self.http.get.call_count, 1)
            self.assertEqual(self.http.post.call_count, 4)
            self.now += 5.0
            self.v.ask(image, "转录")
            self.assertEqual(self.http.get.call_count, 2)

    def test_slow_success_refreshes_readiness_for_the_next_question(self):
        def slow_post(*args, **kwargs):
            self.now += 20.0
            return self.reply
        self.http.post.side_effect = slow_post
        with _support.temp_dir() as td:
            image, _, _ = _support.write_pdf(td, "page.png", b"fake image transport")
            self.v.ask(image, "整页转录")
            self.v.ask(image, "标题栏转录")
            self.assertEqual(self.http.get.call_count, 1)
            self.assertEqual(self.http.post.call_count, 2)
            self.now += 5.0
            self.v.ask(image, "明细表转录")
            self.assertEqual(self.http.get.call_count, 2)

    def test_post_failure_is_not_retried_and_invalidates_readiness(self):
        with _support.temp_dir() as td:
            image, _, _ = _support.write_pdf(td, "page.png", b"fake image transport")
            self.http.post.side_effect = [RuntimeError("offline"), self.reply]
            with self.assertRaisesRegex(self.module.VisionError, "offline"):
                self.v.ask(image, "转录")
            self.assertEqual(self.http.post.call_count, 1)
            self.v.ask(image, "转录")
            self.assertEqual(self.http.get.call_count, 2)
            self.assertEqual(self.http.post.call_count, 2)

    def test_endpoint_changes_and_explicit_health_never_use_stale_cache(self):
        self.v.ensure_server()
        self.v.server = "http://other.invalid:8081"
        self.v.ensure_server()
        self.assertEqual(self.http.get.call_count, 2)
        self.http.get.return_value.status_code = 503
        self.assertFalse(self.v.healthy())
        self.assertEqual(self.http.get.call_count, 3)
        with self.assertRaises(self.module.VisionError):
            self.v.ensure_server()
        self.assertEqual(self.http.get.call_count, 4)


@unittest.skipUnless(all(importlib.util.find_spec(name) is not None for name in ("pypdf", "pypdfium2", "PIL")), "requires optional recognition PDF dependencies")
class RealPdfRecognitionTests(unittest.TestCase):
    def make_pdf(self, path, page_count):
        import pypdf
        writer = pypdf.PdfWriter()
        for _ in range(page_count):
            writer.add_blank_page(width=300, height=200)
        with open(path, "wb") as handle:
            writer.write(handle)

    def test_real_multipage_recognition_splits_once_preserves_page_counts(self):
        from pipeline import pdf_tools, recognize_core
        v = Mock()
        v.ask.side_effect = ["图号: J02.A.001\n名称: 检测报告"] * 2 + ["图号: J02.A.002\n名称: 检测报告"] * 4
        progress = Mock()
        with _support.temp_dir() as td:
            scans = [os.path.join(td, "two.pdf"), os.path.join(td, "four.pdf")]
            for filename, count in zip(scans, (2, 4)):
                self.make_pdf(filename, count)
            with patch.object(pdf_tools, "split_pdf", wraps=pdf_tools.split_pdf) as split:
                items, _, warnings = recognize_core.run_recognition(scans, "", [], v, work_dir=td, log=lambda *a: None, progress=progress)
            self.assertEqual(split.call_count, 2)
            self.assertEqual(v.ask.call_count, 6)
            self.assertEqual([item["page"] for item in items], ["1,2,3,4", "1,2"])
            self.assertEqual(warnings, [])
            self.assertEqual([call.args for call in progress.call_args_list], [(n, 6) for n in range(6)])
            # The production merger wrote readable PDFs with every original page.
            import pypdf
            batch = next(Path(td).glob("server_batch_*"))
            for code, count in (("J02.A.001", 2), ("J02.A.002", 4)):
                self.assertEqual(len(pypdf.PdfReader(batch / ("merged_" + code + ".pdf")).pages), count)

    def test_real_two_page_process_bundle_round_trip(self):
        import pypdf
        with _support.temp_dir() as td:
            pdf = os.path.join(td, "source.pdf")
            self.make_pdf(pdf, 2)
            item = extract.resolve_document({"图号": "Z02.A36.02.ARF25C", "名称": "总装图"})
            item["page_pdf"] = pdf
            manifest_path = drawing_bundle.export_bundle([item], Path(td) / "bundle")
            _, manifest = drawing_bundle.load_bundle(manifest_path)
            actual = manifest["items"][0]
            self.assertEqual(actual["display_name"], "总装图")
            self.assertEqual(actual["name"], "")
            self.assertEqual(len(pypdf.PdfReader(actual["file_path"]).pages), 2)
            self.assertEqual(drawing_bundle.verify_fingerprint(actual), [])


if __name__ == "__main__":
    unittest.main()
