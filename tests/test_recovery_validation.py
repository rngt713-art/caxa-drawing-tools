"""Human recovery requires complete evidence; never contacts CAXA."""
import contextlib
import io
import json
import os
import queue
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import _support

_STUB_DEPS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_stub_deps")
if _STUB_DEPS not in sys.path:
    sys.path.append(_STUB_DEPS)
import journal
import upload_recover


class RecoveryValidationTests(unittest.TestCase):
    def setUp(self):
        self.db = _support.journal_path('validation.sqlite3')
        self.j = journal.TaskJournal(self.db)
        self.task = self.j.create_task(digest='recovery-validation')['task_id']

    def tearDown(self):
        self.j.close()

    def pending(self, kind='upload', key='01.01', **kwargs):
        return self.j.record_operation(self.task, kind, key, status=journal.OP_PENDING,
                                       item_code=key if kind == 'upload' else '', **kwargs)

    def resolve(self, kind='upload', key='01.01', **kwargs):
        return self.j.resolve_operation(self.task, kind, key, result='success', **kwargs)

    def test_missing_upload_evidence_is_rejected_without_mutation(self):
        before = self.pending()
        with self.assertRaises(ValueError):
            self.resolve()
        self.assertEqual(self.j.get_operation(self.task, 'upload', '01.01'), before)

    def test_upload_ids_must_be_positive_integers(self):
        for value in ('', '0', '-1', '2.5', 'nan', True):
            with self.subTest(value=value):
                self.pending()
                with self.assertRaises(ValueError):
                    self.resolve(remote_id=value, class_id=1002, iterator_no='b.2')

    def test_upload_class_and_iteration_are_required(self):
        for cls, iterator in ((None, 'b.2'), (1001, 'b.2'), (1003, 'b.2'), (1002, '')):
            with self.subTest(cls=cls, iterator=iterator):
                self.pending()
                with self.assertRaises(ValueError):
                    self.resolve(remote_id=12, class_id=cls, iterator_no=iterator)

    def test_process_document_requires_capp_document(self):
        self.pending(key='Z03.HW.001')
        with self.assertRaises(ValueError):
            self.resolve(key='Z03.HW.001', remote_id=12, class_id=1002, iterator_no='b.2')
        result = self.resolve(key='Z03.HW.001', remote_id=12, class_id=1003, iterator_no='b.2')
        self.assertEqual(result['status'], journal.OP_OK)

    def test_empty_values_preserve_ids_iteration_and_structured_evidence(self):
        self.pending(remote_id=12, class_id=1002, iterator_no='b.2', detail='{"source":"known"}')
        result = self.resolve(remote_id='', class_id='', note='manually checked')
        self.assertEqual((result['remote_id'], result['class_id'], result['iterator_no']), ('12','1002','b.2'))
        self.assertEqual(json.loads(result['detail'])['source'], 'known')
        self.assertEqual(json.loads(result['detail'])['recovery_note'], 'manually checked')

    def test_relation_needs_matching_type_but_not_document_id(self):
        self.pending('relation', 'P|C', parent_code='P', child_code='C', link_type='borrow')
        with self.assertRaises(ValueError):
            self.resolve('relation', 'P|C', link_type='own')
        result = self.resolve('relation', 'P|C')
        self.assertEqual(result['status'], journal.OP_OK)
        self.assertEqual(result['remote_id'], '')

    def test_relation_missing_type_is_rejected(self):
        self.pending('relation', 'P|C', parent_code='P', child_code='C')
        with self.assertRaises(ValueError):
            self.resolve('relation', 'P|C')

    def test_no_write_preserves_evidence_and_only_changes_addressed_operation(self):
        self.pending(remote_id=12, class_id=1002, iterator_no='b.2')
        other = self.pending(key='01.02')
        result = self.j.resolve_operation(self.task, 'upload', '01.01', result='no_write', note='checked absent')
        self.assertEqual(result['status'], journal.OP_FAILED)
        self.assertEqual(result['remote_id'], '12')
        self.assertEqual(self.j.get_operation(self.task, 'upload', '01.02'), other)

    def workflow(self, drawing_ids=(12, 13)):
        return self.j.record_workflow(self.task, status=journal.OP_PENDING, process_id=501,
                                     drawing_ids=list(drawing_ids), started=False)

    def test_workflow_needs_running_state_and_exact_observed_document_set(self):
        self.workflow()
        for state, ids in ((None, None), ('2', None), ('3', [12,13]), ('2', [12]), ('2', [12,13,14])):
            with self.subTest(state=state, ids=ids):
                with self.assertRaises(ValueError):
                    self.resolve('workflow', 'workflow', process_state=state, linked_document_ids=ids)
        result = self.resolve('workflow', 'workflow', process_state='2', linked_document_ids=[13,12], note='checked')
        payload = json.loads(result['detail'])
        self.assertTrue(payload['started'])
        self.assertTrue(payload['documents_verified'])
        self.assertEqual(payload['drawing_ids'], [12,13])
        self.assertEqual(payload['linked_document_ids'], [12,13])
        self.assertEqual(result['remote_id'], '501')

    def pending_workflow_with_verified_evidence(self):
        return self.pending('workflow', 'workflow', remote_id=501, detail=json.dumps({
            "drawing_ids": [12,13], "linked_document_ids": [12,13],
            "process_state": "2", "started": True,
            "state_verified": True, "documents_verified": True,
        }))

    def test_workflow_omitted_evidence_preserves_known_verified_values(self):
        self.pending_workflow_with_verified_evidence()
        result = self.resolve('workflow', 'workflow')
        self.assertEqual(result['status'], journal.OP_OK)
        payload = json.loads(result['detail'])
        self.assertEqual(payload['process_state'], '2')
        self.assertEqual(payload['linked_document_ids'], [12,13])

    def test_workflow_explicit_invalid_state_does_not_reuse_old_state(self):
        for state in ('', 0, False):
            with self.subTest(state=state):
                before = self.pending_workflow_with_verified_evidence()
                with self.assertRaises(ValueError):
                    self.resolve('workflow', 'workflow', process_state=state)
                self.assertEqual(self.j.workflow(self.task), before)

    def test_workflow_explicit_empty_documents_do_not_reuse_old_documents(self):
        before = self.pending_workflow_with_verified_evidence()
        with self.assertRaises(ValueError):
            self.resolve('workflow', 'workflow', linked_document_ids=[])
        self.assertEqual(self.j.workflow(self.task), before)

    def test_workflow_without_expected_document_set_cannot_be_confirmed(self):
        self.workflow(())
        with self.assertRaises(ValueError):
            self.resolve('workflow', 'workflow', process_state='2', linked_document_ids=[12])

    def test_cli_rejects_missing_evidence_with_friendly_error(self):
        self.pending()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = upload_recover.main(['--journal', self.db, 'confirm', '--task', self.task,
                                       '--kind', 'upload', '--key', '01.01'])
        self.assertEqual(code, 2)
        self.assertIn('失败：', output.getvalue())
        self.assertEqual(self.j.get_operation(self.task, 'upload', '01.01')['status'], journal.OP_PENDING)

    def test_cli_can_confirm_workflow_with_complete_evidence(self):
        self.workflow()
        with contextlib.redirect_stdout(io.StringIO()):
            code = upload_recover.main(['--journal', self.db, 'confirm', '--task', self.task,
                                       '--kind','workflow','--key','workflow','--process-state','2',
                                       '--document-ids','12','13'])
        self.assertEqual(code, 0)
        self.assertEqual(self.j.workflow(self.task)['status'], journal.OP_OK)

    def test_ui_upload_recovery_never_prompts_for_hidden_ids(self):
        import upload_main
        self.pending()
        real_factory = journal.TaskJournal
        verifier = SimpleNamespace(
            verify_upload=Mock(return_value={
                'remote_id': 123,
                'class_id': 1002,
                'iterator_no': 'b.2',
            }),
            close=Mock(),
        )
        fake_app = SimpleNamespace(
            events=queue.Queue(),
            log=Mock(),
        )
        settings = {
            'ip': '127.0.0.1',
            'port': 8900,
            'user': 'tester',
            'password': 'unused',
            'start_workflow': False,
            'performer': '',
        }
        selected = [(
            self.j.get_task(self.task),
            self.j.get_operation(self.task, 'upload', '01.01'),
        )]
        with patch.object(
            upload_main.journal_module,
            'TaskJournal',
            side_effect=lambda: real_factory(self.db),
        ), patch.object(
            upload_main.RecoveryVerifier,
            'connect',
            return_value=verifier,
        ):
            upload_main.UploadApp._recovery_worker(
                fake_app,
                selected,
                settings,
                {},
            )
        verifier.verify_upload.assert_called_once()
        event_kind, payload = fake_app.events.get_nowait()
        while event_kind == 'status':
            event_kind, payload = fake_app.events.get_nowait()
        self.assertEqual(event_kind, 'recovery_done')
        self.assertEqual(len(payload[0]), 1)
        self.assertEqual(payload[1], [])
        result = self.j.get_operation(self.task, 'upload', '01.01')
        self.assertEqual(result['status'], journal.OP_OK)
        self.assertEqual(result['remote_id'], '123')
        self.assertEqual(result['class_id'], '1002')

    def test_ui_no_write_retry_releases_operation_and_starts_upload(self):
        import upload_main
        real_factory = journal.TaskJournal
        item = {
            'code': '01.01',
            'status': '待核实',
            'drawing_id': 9,
        }
        fake_app = SimpleNamespace(
            busy=False,
            items=[item],
            relations=[],
            bundle={'items': [item], 'relations': []},
            _blocked_by_busy=lambda: False,
            _build_settings=lambda: {
                'ip': '127.0.0.1',
                'port': 8900,
                'user': 'tester',
                'password': 'unused',
                'start_workflow': False,
                'performer': '',
            },
            set_busy=Mock(),
            refresh_tree=Mock(),
            log=Mock(),
            status_var=Mock(),
            after=Mock(),
            start_upload=Mock(),
        )
        settings = fake_app._build_settings()
        self.task = self.j.create_task(
            digest=upload_main.content_digest(fake_app.bundle, settings),
            server=settings["ip"],
            port=settings["port"],
            user=settings["user"],
        )["task_id"]
        self.pending()
        selected = [(
            self.j.get_task(self.task),
            self.j.get_operation(self.task, 'upload', '01.01'),
        )]
        with patch.object(
            upload_main.journal_module,
            'TaskJournal',
            side_effect=lambda: real_factory(self.db),
        ), patch.object(
            upload_main,
            'choose_pending_recovery_action',
            return_value=('no_write_retry', selected),
        ):
            upload_main.UploadApp.recover_pending(fake_app)
        result = self.j.get_operation(self.task, 'upload', '01.01')
        self.assertEqual(result['status'], journal.OP_FAILED)
        self.assertEqual(item['status'], '待上传')
        fake_app.after.assert_called_once_with(0, fake_app.start_upload)

    def test_pending_tasks_are_filtered_before_recent_task_limit(self):
        self.pending()
        for index in range(21):
            self.j.create_task(digest="new-%d" % index)
        tasks = self.j.list_tasks_with_pending_operations()
        self.assertEqual([task["task_id"] for task in tasks], [self.task])

    def test_ui_verify_returns_to_event_loop_before_caxa_connect(self):
        import upload_main
        self.pending()
        real_factory = journal.TaskJournal
        selected = [(
            self.j.get_task(self.task),
            self.j.get_operation(self.task, 'upload', '01.01'),
        )]
        fake_app = SimpleNamespace(
            busy=False,
            items=[],
            relations=[],
            bundle={'items': [], 'relations': []},
            _blocked_by_busy=lambda: False,
            _build_settings=lambda: {
                'ip': '127.0.0.1',
                'port': 8900,
                'user': 'tester',
                'password': 'unused',
                'start_workflow': False,
                'performer': '',
            },
            set_busy=Mock(),
            status_var=Mock(),
            _recovery_worker=Mock(),
        )
        worker = SimpleNamespace(start=Mock())
        with patch.object(
            upload_main.journal_module,
            'TaskJournal',
            side_effect=lambda: real_factory(self.db),
        ), patch.object(
            upload_main,
            'choose_pending_recovery_action',
            return_value=('verify', selected),
        ), patch.object(
            upload_main.threading,
            'Thread',
            return_value=worker,
        ), patch.object(
            upload_main.RecoveryVerifier,
            'connect',
        ) as connect, patch.object(
            upload_main.messagebox,
            'showerror',
        ) as error:
            upload_main.UploadApp.recover_pending(fake_app)
        error.assert_not_called()
        fake_app.set_busy.assert_called_once_with(True)
        worker.start.assert_called_once()
        connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
