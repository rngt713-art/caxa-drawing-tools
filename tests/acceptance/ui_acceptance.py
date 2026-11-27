"""Real Tk widget smoke tests; no helper executable or network calls."""
import sys, tempfile, unittest, json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).parent
APP=Path(sys.argv.pop(1)).resolve() if len(sys.argv)>1 and not sys.argv[1].startswith('-') else ROOT/'dsh_fix'/'app'
sys.path[:0]=[str(ROOT/'test-deps'),str(APP)]
import upload_main as ui
class UIAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT)
        self.patches=[patch.object(ui.config,'load',return_value={}),patch.object(ui.config,'save'),patch.object(ui.messagebox,'showwarning'),patch.object(ui.messagebox,'showinfo'),patch.object(ui.messagebox,'showerror')]
        for p in self.patches:p.start()
        self.app=ui.UploadApp(); self.app.withdraw(); self.app.update_idletasks()
        self.f=Path(self.tmp.name)/'01.01 test.pdf';self.f.write_bytes(b'PDF')
    def tearDown(self):
        self.app.destroy()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def test_create_add_freeze_unfreeze(self):
        a=self.app; a.add_pdfs([str(self.f)]);self.assertEqual(len(a.items),1)
        a.set_busy(True)
        a.add_pdfs([str(self.f)]); self.assertEqual(len(a.items),1)
        self.assertTrue(all(str(b.cget('state'))=='disabled' for b in a.action_buttons))
        a.set_busy(False); self.assertTrue(all(str(b.cget('state'))=='normal' for b in a.action_buttons))
    def test_collapsible_panels_preserve_settings_and_log(self):
        a = self.app
        self.assertEqual(a.settings_frame.winfo_manager(), "pack")
        a.ip_var.set("192.0.2.1")
        a.settings_button.invoke()
        self.assertFalse(a.settings_frame.winfo_manager())
        a.settings_button.invoke()
        self.assertEqual(a.ip_var.get(), "192.0.2.1")
        self.assertFalse(a.log_frame.winfo_manager())
        a._append_ui_log("离线测试")
        a.log_button.invoke()
        self.assertEqual(a.log_frame.winfo_manager(), "pack")
        self.assertIn("离线测试", a.log_text.get("1.0", "end"))
        a.log_button.invoke()
        self.assertFalse(a.log_frame.winfo_manager())
    def test_manual_bundle_keeps_one_batch_id_when_rows_are_added(self):
        a=self.app
        a.add_pdfs([str(self.f)])
        first=a.bundle.get('batch_id')
        second=Path(self.tmp.name)/'01.02 second.pdf'
        second.write_bytes(b'PDF-2')
        a.add_pdfs([str(second)])
        self.assertTrue(first)
        self.assertEqual(a.bundle.get('batch_id'),first)
    def test_canonical_delete_then_refresh(self):
        a=self.app; a.add_pdfs([str(self.f)],parent_code='01.99')
        self.assertEqual(len(a.relations),1)
        relation_iid=next(iid for iid,rel in a._tree_relations.items() if rel)
        a.tree.selection_set(relation_iid); a.delete_selected_relation()
        a.refresh_tree(); self.assertEqual(a.relations,[])
    def test_busy_toggle_and_delete_no_mutation(self):
        a=self.app; a.add_pdfs([str(self.f)],parent_code='01.99')
        relation_iid=next(iid for iid,rel in a._tree_relations.items() if rel)
        a.tree.selection_set(relation_iid); before=json.dumps(a.relations,sort_keys=True)
        a.set_busy(True); a.toggle_selected_relation_type(); a.delete_selected_relation()
        self.assertEqual(json.dumps(a.relations,sort_keys=True),before)
    def test_busy_bundle_load_no_mutation(self):
        a=self.app;a.add_pdfs([str(self.f)]);a.set_busy(True)
        with patch.object(ui.drawing_bundle,'load_bundle') as loader:
            a._load_bundle_from_path('unused');self.assertFalse(loader.called)
    def test_event_handler_error_still_reschedules_polling(self):
        a=self.app
        a.events.put(('refresh',None))
        with patch.object(a,'refresh_tree',side_effect=RuntimeError('boom')), patch.object(
            a,'after',return_value='next'
        ) as after:
            a._poll_events()
        after.assert_called_once_with(150,a._poll_events)
    def test_duplicate_policy_uses_chinese_labels(self):
        a=self.app
        self.assertEqual(tuple(a.duplicate_combo.cget('values')),('升版','复用'))
        self.assertEqual(a.duplicate_var.get(),'升版')
        a.duplicate_combo.current(1)
        a.ip_var.set("192.0.2.22")
        a.user_var.set("operator")
        self.assertEqual(a._build_settings()["duplicate_policy"], "reuse")
    def test_workflow_only_button_is_available_in_action_bar(self):
        labels=[str(button.cget('text')) for button in self.app.action_buttons]
        self.assertIn('发起审核打印',labels)
    def test_update_button_is_integrated_and_never_auto_downloads(self):
        self.assertIn(self.app.update_button,self.app.action_buttons)
        text=str(self.app.update_button.cget('text'))
        self.assertTrue(text=='检查更新' or text.startswith('下载 v'))
        self.assertFalse(self.app._automatic_update_check_enabled())
    def test_daily_update_refreshes_yesterdays_cache(self):
        a = self.app
        old_release = ui.update_checker.UpdateInfo(
            current_version="1.2.2", latest_version="1.3.0", available=True,
            tag_name="v1.3.0", asset_name="图纸上传助手.exe",
            download_url="https://example.test/old.exe", sha256="a" * 64,
        )
        cache = ui.update_checker.UpdateStateStore(Path(self.tmp.name) / "update.json")
        cache.mark_attempt(success=True, info=old_release, today=ui.update_checker.date.min)
        with patch.object(ui.update_checker, "UpdateStateStore", return_value=cache):
            with patch.object(a, "_automatic_update_check_enabled", return_value=True):
                with patch.object(a, "after", return_value="daily") as after:
                    a._init_update_state()
        self.assertIsNone(a._available_update)
        after.assert_called_once_with(700, a._start_daily_update_check)
        with patch.object(a, "_start_update_check") as check_update:
            a._start_daily_update_check()
        check_update.assert_called_once_with(manual=False)

    def test_download_failure_returns_update_button_to_check(self):
        a = self.app
        release = ui.update_checker.UpdateInfo(
            current_version="1.2.2", latest_version="1.3.0", available=True,
            tag_name="v1.3.0", asset_name="图纸上传助手.exe",
            download_url="https://example.test/missing.exe", sha256="a" * 64,
        )
        path = Path(self.tmp.name) / "update.json"
        a.update_state = ui.update_checker.UpdateStateStore(path)
        a.update_state.mark_attempt(success=True, info=release)
        a._available_update = release
        a._handle_update_download_error((release, ui.update_checker.UpdateError("HTTP 404")))
        cache = ui.update_checker.UpdateStateStore(path)
        self.assertIsNone(cache.cached_update("1.2.2"))
        self.assertFalse(cache.checked_today())
        self.assertEqual(a.update_button.cget("text"), "检查更新")
        with patch.object(a, "_start_update_check") as check_update:
            a.on_update_button()
        check_update.assert_called_once_with(manual=True)

    def test_update_relaunch_keeps_writes_blocked_until_exit(self):
        a = self.app
        a._update_busy = True
        with patch.object(a, "after") as after:
            a._handle_update_relaunch_ready((None, "new.exe"))
        after.assert_called_once_with(250, a.destroy)
        self.assertTrue(a._blocked_by_update())

    def test_completed_workflow_only_does_not_offer_reupload(self):
        a = self.app
        a.events.put(("upload_done", ({}, {"ok": True, "duplicate": True}, {}, True)))
        with patch.object(ui.messagebox, "askyesno") as confirm:
            a._poll_events()
        confirm.assert_not_called()

    def test_downloaded_update_waits_for_active_upload(self):
        path=Path(self.tmp.name)/'CAXA-Drawing-Uploader-v1.2.0.exe'
        path.write_bytes(b'exe')
        info=ui.update_checker.UpdateInfo(
            current_version='1.1.0',latest_version='1.2.0',available=True,
            tag_name='v1.2.0',asset_name=path.name,
            download_url='https://example.test/app.exe',sha256='a'*64,
        )
        self.app.busy=True
        with patch.object(ui.threading,'Thread') as thread:
            self.app._handle_update_downloaded((info,path))
        thread.assert_not_called()
        self.assertIsNotNone(self.app._pending_update)
        self.app.busy=False
        with patch.object(ui.threading,'Thread') as thread:
            self.app.set_busy(False)
        thread.assert_called_once()
    def test_existing_original_upgrade_only_cell_toggles(self):
        a=self.app
        path=Path(self.tmp.name)/'01.01-01 child.pdf'; path.write_bytes(b'PDF-C')
        a.add_pdfs([str(path)])
        item=a.items[0]
        item['existing_original']=True
        item['existing_original_part_id']=501
        item['upgrade_only']=True
        a.refresh_tree()
        iid=next(iid for iid,code in a._tree_codes.items() if code=='01.01-01')
        self.assertIn('已有原件',a.tree.item(iid,'values')[2])
        self.assertIn('☑',a.tree.item(iid,'values')[7])
        a.deiconify()
        a.update()
        a.tree.see(iid)
        a.update_idletasks()
        x, y, width, height = a.tree.bbox(iid, "upgrade_only")
        point = SimpleNamespace(x=x + width // 2, y=y + height // 2)
        self.assertEqual(a.tree.identify("region", point.x, point.y), "cell")
        self.assertEqual(a.tree.identify_row(point.y), iid)
        a._toggle_upload(point)
        self.assertFalse(item['upgrade_only'])
        self.assertIn('☐',a.tree.item(iid,'values')[7])
    def test_existing_own_relation_is_described_and_upgrades_only(self):
        a=self.app
        path=Path(self.tmp.name)/'01.01-01 child.pdf'
        path.write_bytes(b'PDF-C')
        a.add_pdfs([str(path)])
        item=a.items[0]
        item.update({
            'existing_original':True,
            'existing_original_part_id':501,
            'existing_relation':True,
            'existing_relation_type':'own',
            'upgrade_only':True,
        })
        a.refresh_tree()
        iid=next(
            iid for iid,code in a._tree_codes.items()
            if code=='01.01-01'
        )
        self.assertIn('已有归属关系',a.tree.item(iid,'values')[2])
        self.assertEqual(a.tree.item(iid,'values')[7],'☑ 仅升版')
    def test_add_relation_dialog_is_centered(self):
        with patch.object(self.app,'_center_dialog_on_parent') as center:
            self.app.add_relation_dialog('01.01')
        center.assert_called_once()
    def test_relation_rows_are_nested_and_highlight_clears(self):
        a=self.app
        parent=Path(self.tmp.name)/'01.01 parent.pdf'; parent.write_bytes(b'PDF-P')
        child=Path(self.tmp.name)/'01.01-01 child.pdf'; child.write_bytes(b'PDF-C')
        a.add_pdfs([str(parent)])
        a.add_pdfs([str(child)],parent_code='01.01')
        parent_iid=next(iid for iid,code in a._tree_codes.items() if code=='01.01')
        child_iid=next(iid for iid,rel in a._tree_relations.items() if rel)
        self.assertEqual(a.tree.parent(child_iid),parent_iid)
        relation=next(rel for rel in a.relations if rel['child_code']=='01.01-01')
        self.assertEqual(relation['link_type'],'own')
        self.assertFalse(next(item for item in a.items if item['code']=='01.01-01')['borrow'])
        a._set_drag_target(parent_iid)
        self.assertIn('drop_target',a.tree.item(parent_iid,'tags'))
        a._drag_source_row=parent_iid
        a._show_drag_ghost(SimpleNamespace(x_root=100,y_root=100))
        self.assertIsNotNone(a._drag_ghost)
        a._clear_drag_visuals()
        self.assertIsNone(a._drag_ghost)
        self.assertNotIn('drop_target',a.tree.item(parent_iid,'tags'))
    def test_multi_select_drag_adds_borrow_relations(self):
        a=self.app
        parent1=Path(self.tmp.name)/'02.01 first-parent.pdf'; parent1.write_bytes(b'PDF-P1')
        parent2=Path(self.tmp.name)/'02.02 second-parent.pdf'; parent2.write_bytes(b'PDF-P2')
        child1=Path(self.tmp.name)/'01.01-01 child-one.pdf'; child1.write_bytes(b'PDF-C1')
        child2=Path(self.tmp.name)/'01.01-02 child-two.pdf'; child2.write_bytes(b'PDF-C2')
        a.add_pdfs([str(parent1)])
        a.add_pdfs([str(parent2)])
        a.add_pdfs([str(child1),str(child2)],parent_code='02.01')
        child_iids=[iid for iid,item in a._tree_items.items() if item and item.get('code','').startswith('01.01-')]
        parent2_iid=next(iid for iid,code in a._tree_codes.items() if code=='02.02')
        a.tree.selection_set(*child_iids)
        with patch.object(a.tree,'identify_row',return_value=child_iids[0]),patch.object(a.tree,'identify_column',return_value='#0'):
            a._begin_row_drag(SimpleNamespace(x=5,y=5,x_root=100,y_root=100,state=0))
        self.assertEqual(len(a._drag_source_rows),2)
        a._drag_started=True; a._drag_target_row=parent2_iid
        a._finish_row_drag(SimpleNamespace(y=5))
        relation_pairs={(rel['parent_code'],rel['child_code']) for rel in a.relations}
        self.assertEqual(
            relation_pairs,
            {
                ('02.01','01.01-01'),
                ('02.01','01.01-02'),
                ('02.02','01.01-01'),
                ('02.02','01.01-02'),
            },
        )
        for item in a.items:
            if item.get('code','').startswith('01.01-'):
                self.assertTrue(item['borrow'])
    def test_one_owner_plus_multiple_borrow_parents(self):
        a=self.app
        child=Path(self.tmp.name)/'04.06.OV855-01 component.pdf'; child.write_bytes(b'PDF-C')
        a.add_pdfs([str(child)])
        a._add_relation('04.06','04.06.OV855-01')
        a._add_relation('03.OV.04-05030','04.06.OV855-01')
        a._add_relation('03.HE.002035A-21B01','04.06.OV855-01')
        own=[rel for rel in a.relations if rel['link_type']=='own']
        borrow=[rel for rel in a.relations if rel['link_type']=='borrow']
        self.assertEqual([(rel['parent_code'],rel['child_code']) for rel in own],[('04.06','04.06.OV855-01')])
        self.assertEqual(len(borrow),2)
        a._add_relation('04.06.OV855','04.06.OV855-01')
        own=[rel for rel in a.relations if rel['link_type']=='own']
        self.assertEqual([rel['parent_code'] for rel in own],['04.06.OV855'])
        self.assertEqual(sum(rel['link_type']=='borrow' for rel in a.relations),2)
    def test_import_auto_builds_immediate_code_hierarchy(self):
        a=self.app
        rows=[
            ('01.21.01.SFD2-17','钣金'),
            ('01.21.01.SFD2-17-01','防爆箱主体'),
            ('01.21.01.SFD2-17-01-01','左侧板'),
            ('01.21.01.SFD2-17-01-02','中间板'),
            ('01.21.01.SFD2-17-01-03','右侧板'),
            ('01.21.01.SFD2-17-01-03-01','右侧板主体'),
            ('01.21.01.SFD2-17-01-03-02','气弹簧支架'),
        ]
        paths=[]
        for code,name in rows:
            path=Path(self.tmp.name)/('%s %s.pdf'%(code,name)); path.write_bytes(b'PDF-'+code.encode())
            paths.append(str(path))
        a.add_pdfs(paths)
        relation_pairs={(rel['parent_code'],rel['child_code']) for rel in a.relations}
        self.assertIn(('01.21.01.SFD2-17','01.21.01.SFD2-17-01'),relation_pairs)
        self.assertIn(('01.21.01.SFD2-17-01','01.21.01.SFD2-17-01-03'),relation_pairs)
        self.assertIn(('01.21.01.SFD2-17-01-03','01.21.01.SFD2-17-01-03-01'),relation_pairs)
        self.assertNotIn(('01.21.01.SFD2-17','01.21.01.SFD2-17-01-03-01'),relation_pairs)
        def row(code):
            return next(iid for iid,value in a._tree_codes.items() if value==code)
        self.assertEqual(a.tree.parent(row('01.21.01.SFD2-17-01')),row('01.21.01.SFD2-17'))
        self.assertEqual(a.tree.parent(row('01.21.01.SFD2-17-01-03')),row('01.21.01.SFD2-17-01'))
        self.assertEqual(a.tree.parent(row('01.21.01.SFD2-17-01-03-01')),row('01.21.01.SFD2-17-01-03'))
    def test_double_click_indicator_does_not_open_editor(self):
        a=self.app
        event=SimpleNamespace(x=10,y=10)
        with patch.object(a.tree,'identify',return_value='tree'),patch.object(a.tree,'identify_element',return_value='Treeitem.indicator'),patch.object(a,'_edit_item') as editor:
            a._on_tree_double_click(event)
            editor.assert_not_called()
        with patch.object(a.tree,'identify',return_value='tree'),patch.object(a.tree,'identify_element',return_value='Treeitem.text'),patch.object(a,'_edit_item') as editor:
            a._on_tree_double_click(event)
            editor.assert_called_once_with(event)
    def test_multiple_03_machines_have_independent_product_groups(self):
        a=self.app
        machine1=Path(self.tmp.name)/'03.HW.015350-46P01 空气压缩机.pdf'; machine1.write_bytes(b'PDF-M1')
        machine2=Path(self.tmp.name)/'03.OF.005-06012 氧气压缩机.pdf'; machine2.write_bytes(b'PDF-M2')
        component=Path(self.tmp.name)/'04.13.M3310-09 传动组件.pdf'; component.write_bytes(b'PDF-C')
        a.add_pdfs([str(machine1),str(machine2),str(component)])
        by_code={item['code']:item for item in a.items}
        by_code['03.HW.015350-46P01']['series_product_name']='HW1.5-35(直连/水冷)'
        by_code['03.OF.005-06012']['series_product_name']='OF0.5-6(氧气)'
        a.refresh_tree()
        groups=a._tree_product_groups
        self.assertEqual(set(groups.values()),{'HW系列','OF系列'})
        for group_iid,folder in groups.items():
            code=next(code for code in ['03.HW.015350-46P01','03.OF.005-06012'] if code.split('.')[1]+'系列'==folder)
            product_iid=next(iid for iid,value in a._tree_product_nodes.items() if value==code)
            machine_iid=next(iid for iid,value in a._tree_codes.items() if value==code)
            self.assertEqual(a.tree.parent(product_iid),group_iid)
            self.assertEqual(a.tree.parent(machine_iid),product_iid)
        self.assertNotIn('series_product_name',by_code['04.13.M3310-09'])
    def test_process_machine_and_borrowed_component_share_hx_product_group(self):
        a=self.app
        process=Path(self.tmp.name)/'Z03.HX21.E20H-03L.pdf'
        component=Path(self.tmp.name)/'04.13.HX21E20H-06 传动组件.pdf'
        process.write_bytes(b'PDF-Z')
        component.write_bytes(b'PDF-C')
        a.add_pdfs([str(process)])
        a.add_pdfs([str(component)],parent_code='03.HX21.E20H-03L')
        groups={path:iid for iid,path in a._tree_product_groups.items()}
        self.assertIn('HX系列',groups)
        self.assertNotIn('HX21系列',groups)
        product_iid=next(
            iid for iid,code in a._tree_product_nodes.items()
            if code=='03.HX21.E20H-03L'
        )
        process_iid=next(
            iid for iid,code in a._tree_codes.items()
            if code=='Z03.HX21.E20H-03L'
        )
        machine_iid=next(
            iid for iid,code in a._tree_codes.items()
            if code=='03.HX21.E20H-03L'
        )
        component_iid=next(
            iid for iid,code in a._tree_codes.items()
            if code=='04.13.HX21E20H-06'
        )
        self.assertEqual(a.tree.parent(product_iid),groups['HX系列'])
        self.assertEqual(a.tree.parent(process_iid),product_iid)
        self.assertEqual(a.tree.parent(machine_iid),product_iid)
        self.assertEqual(a.tree.parent(component_iid),machine_iid)
    def test_w6_requires_manual_series_selection(self):
        a=self.app
        machine=Path(self.tmp.name)/'03.W6.001 设计图.pdf'
        machine.write_bytes(b'PDF-W6')
        a.add_pdfs([str(machine)])
        groups={path:iid for iid,path in a._tree_product_groups.items()}
        self.assertIn('未指定系列',groups)
        self.assertNotIn('W系列',groups)
        a._apply_inline_series(groups['未指定系列'],'W6')
        self.assertEqual(
            a._item_by_code('03.W6.001')['series_product_folder'],
            'W6',
        )
    def test_ht_hgb_uses_nested_product_folder(self):
        a=self.app
        hgb=Path(self.tmp.name)/'03.HT.HGB.001 HGB压缩机.pdf'; hgb.write_bytes(b'PDF-HGB')
        other=Path(self.tmp.name)/'03.HT.ABC.001 合同压缩机.pdf'; other.write_bytes(b'PDF-HT')
        a.add_pdfs([str(hgb),str(other)])
        by_path={path:iid for iid,path in a._tree_product_groups.items()}
        self.assertIn('HT合同特殊',by_path)
        self.assertIn('HT合同特殊/HGB系列',by_path)
        self.assertEqual(
            a.tree.parent(by_path['HT合同特殊/HGB系列']),
            by_path['HT合同特殊'],
        )
        hgb_iid=next(iid for iid,code in a._tree_codes.items() if code=='03.HT.HGB.001')
        other_iid=next(iid for iid,code in a._tree_codes.items() if code=='03.HT.ABC.001')
        hgb_product_iid=next(iid for iid,value in a._tree_product_nodes.items() if value=='03.HT.HGB.001')
        other_product_iid=next(iid for iid,value in a._tree_product_nodes.items() if value=='03.HT.ABC.001')
        self.assertEqual(a.tree.parent(hgb_product_iid),by_path['HT合同特殊/HGB系列'])
        self.assertEqual(a.tree.parent(other_product_iid),by_path['HT合同特殊'])
        self.assertEqual(a.tree.parent(hgb_iid),hgb_product_iid)
        self.assertEqual(a.tree.parent(other_iid),other_product_iid)
    def test_inline_product_edit_survives_refresh_and_enters_upload_snapshot(self):
        a = self.app
        first_code, second_code = "03.HW.001", "03.HW.002"
        for name in ("03.HW.001 示例整机.pdf", "03.HW.002 第二台整机.pdf", "Z03.HW.001.pdf"):
            path = Path(self.tmp.name) / name
            path.write_bytes(b"PDF")
            a.add_pdfs([str(path)])

        a._set_series_product_required({first_code})
        a.deiconify()
        a.update()
        first = next(row for row, code in a._tree_product_nodes.items() if code == first_code)
        self.assertEqual(a.tree.set(first, "status"), "必填")
        a.tree.see(first)
        a.update_idletasks()
        x, y, width, height = a.tree.bbox(first, "#0")
        event = SimpleNamespace(x=x + width // 2, y=y + height // 2)
        self.assertEqual(a._on_tree_double_click(event), "break")
        a._inline_editor.insert(0, "首台产品")
        a._inline_editor.event_generate("<FocusOut>")
        a.update()
        a.refresh_tree()
        first = next(row for row, code in a._tree_product_nodes.items() if code == first_code)
        self.assertEqual(a.tree.set(first, "status"), "已命名")
        self.assertEqual(a.tree.item(first, "text"), "系列产品 首台产品")
        self.assertEqual(a._item_by_code("Z03.HW.001")["series_product_name"], "首台产品")

        second = next(row for row, code in a._tree_product_nodes.items() if code == second_code)
        a.tree.selection_set(second)
        a._begin_inline_edit(second, mode="product")
        a._inline_editor.insert(0, "第二台产品")
        a.ip_var.set("192.0.2.1")
        a.user_var.set("test")
        with patch.object(a, "_save_settings", return_value=True), patch.object(ui.threading, "Thread") as worker:
            a.start_upload()
        worker.assert_called_once()
        snapshot = worker.call_args.kwargs["args"][0]
        self.assertEqual(
            {item["code"]: item["series_product_name"] for item in snapshot["items"]},
            {first_code: "首台产品", second_code: "第二台产品", "Z03.HW.001": "首台产品"},
        )

    def test_product_node_context_menu_has_rename_edit(self):
        a=self.app
        machine=Path(self.tmp.name)/'03.HW.015350-46P01 空气压缩机.pdf'; machine.write_bytes(b'PDF-M')
        a.add_pdfs([str(machine)])
        product_iid=next(iid for iid,code in a._tree_product_nodes.items() if code=='03.HW.015350-46P01')
        machine_iid=next(iid for iid,code in a._tree_codes.items() if code=='03.HW.015350-46P01')
        class FakeMenu:
            def __init__(self,*args,**kwargs):
                self.labels = []
                self.commands = {}
            def add_command(self,label,command):
                self.labels.append(label)
                self.commands[label] = command
            def add_separator(self): self.labels.append('---')
            def tk_popup(self,*args): pass
        fake=FakeMenu()
        with patch.object(ui.tk,'Menu',return_value=fake),patch.object(a.tree,'identify_row',return_value=product_iid):
            a._show_row_menu(SimpleNamespace(y=10,x_root=1,y_root=1))
        self.assertIn('修改完整信息…',fake.labels)
        with patch.object(a.tree, "bbox", return_value=(5, 5, 300, 28)):
            fake.commands["修改/新建计划产品名称…"]()
        a._inline_editor.insert(0, "右键编辑名称")
        a._commit_inline_edit()
        a.refresh_tree()
        self.assertEqual(a._item_by_code("03.HW.015350-46P01")["series_product_name"], "右键编辑名称")
        fake2=FakeMenu()
        with patch.object(ui.tk,'Menu',return_value=fake2),patch.object(a.tree,'identify_row',return_value=machine_iid):
            a._show_row_menu(SimpleNamespace(y=10,x_root=1,y_root=1))
        self.assertIn('原地重命名 / 修改文件名…',fake2.labels)
        series_iid=next(iid for iid,path in a._tree_product_groups.items() if path=='HW系列')
        fake3=FakeMenu()
        with patch.object(ui.tk,'Menu',return_value=fake3),patch.object(a.tree,'identify_row',return_value=series_iid):
            a._show_row_menu(SimpleNamespace(y=10,x_root=1,y_root=1))
        self.assertIn('修改上传目标系列…',fake3.labels)
    def test_drawing_context_menu_preserves_multi_selection_and_offers_plan_delete(self):
        a=self.app
        parent=Path(self.tmp.name)/'01.01 parent.pdf'; parent.write_bytes(b'PDF-P')
        child=Path(self.tmp.name)/'01.01-01 child.pdf'; child.write_bytes(b'PDF-C')
        a.add_pdfs([str(parent)])
        a.add_pdfs([str(child)],parent_code='01.01')
        selected=[
            iid for iid,item in a._tree_items.items()
            if item is not None
        ]
        a.tree.selection_set(*selected)
        class FakeMenu:
            def __init__(self,*args,**kwargs): self.labels=[]
            def add_command(self,label,command): self.labels.append(label)
            def add_separator(self): self.labels.append('---')
            def tk_popup(self,*args): pass
        fake=FakeMenu()
        with patch.object(ui.tk,'Menu',return_value=fake),patch.object(a.tree,'identify_row',return_value=selected[0]):
            a._show_row_menu(SimpleNamespace(y=10,x_root=1,y_root=1))
        self.assertEqual(set(a.tree.selection()),set(selected))
        self.assertIn('从列表中删除所选图纸',fake.labels)
    def test_delete_selected_drawings_only_changes_upload_plan(self):
        a=self.app
        machine_path=Path(self.tmp.name)/'03.HW.001 空气压缩机.pdf'; machine_path.write_bytes(b'PDF-M')
        child_path=Path(self.tmp.name)/'04.001 组件.pdf'; child_path.write_bytes(b'PDF-C')
        process_path=Path(self.tmp.name)/'Z03.HW.001.pdf'; process_path.write_bytes(b'PDF-Z')
        a.add_pdfs([str(machine_path),str(child_path),str(process_path)])
        a._add_relation('03.HW.001','04.001',link_type='borrow',manual_override=True)
        a.manual_borrows=[{'machine':'03.HW.001','codes':['04.001','04.999']}]
        a.bundle['manual_borrows']=a.manual_borrows
        a._set_series_product_required({'03.HW.001'})
        a._select_machine_code('03.HW.001')
        drawing_iids=[
            iid for iid,item in a._tree_items.items()
            if item is not None
        ]
        a.tree.selection_set(*drawing_iids)
        with patch.object(ui.messagebox,'askyesno',return_value=True):
            a.delete_selected_drawings()
        self.assertEqual(a.items,[])
        self.assertEqual(a.relations,[])
        self.assertEqual(a.manual_borrows,[])
        self.assertEqual(a.bundle['items'],[])
        self.assertEqual(a.bundle['relations'],[])
        self.assertEqual(a.bundle['manual_borrows'],[])
        self.assertEqual(a._series_product_required_codes,set())
        self.assertTrue(machine_path.exists())
        self.assertTrue(child_path.exists())
        self.assertTrue(process_path.exists())
    def test_edit_dialog_shows_business_terms_and_saves_internal_values(self):
        a = self.app
        path = Path(self.tmp.name) / "Z03.HW.001.pdf"
        path.write_bytes(b"%PDF-test")
        a.add_pdfs([str(path)])
        row = next(row for row, code in a._tree_codes.items() if code == "Z03.HW.001")
        a.tree.selection_set(row)
        a._edit_item()
        dialog = next(w for w in a.winfo_children() if isinstance(w, ui.tk.Toplevel))
        form = dialog.winfo_children()[0]
        kind = form.grid_slaves(row=3, column=1)[0]
        relation = form.grid_slaves(row=6, column=1)[0]
        self.assertEqual(kind.get(), "工艺文档")
        self.assertEqual(tuple(kind.cget("values")), ("工艺文档", "整机图纸", "组件图纸", "未知"))
        self.assertEqual(tuple(relation.cget("values")), ("无上级关系", "归属", "借用"))
        relation.current(2)
        parent = form.grid_slaves(row=7, column=1)[0]
        parent.insert(0, "03.HW.001")
        form.grid_slaves(row=8, column=0)[0].invoke()
        self.assertEqual(a.items[0]["kind"], "process_doc")
        self.assertEqual(a.relations[0]["link_type"], "borrow")

    def test_placeholder_text_is_never_written_to_bound_value(self):
        value=ui.tk.StringVar(master=self.app)
        entry=ui.PlaceholderEntry(
            self.app,
            textvariable=value,
            placeholder='HW1.5-35(直连/水冷)',
        )
        entry.pack()
        self.app.update_idletasks()
        self.assertTrue(entry.placeholder_visible)
        self.assertEqual(value.get(),'')
        self.assertEqual(entry.get(),'HW1.5-35(直连/水冷)')
        entry._on_focus_in()
        self.assertFalse(entry.placeholder_visible)
        entry.insert('end','HW2.5-40')
        self.assertEqual(value.get(),'HW2.5-40')
        entry.delete(0,'end')
        entry._on_focus_out()
        self.assertEqual(value.get(),'')
        self.assertTrue(entry.placeholder_visible)
        entry.destroy()
    def test_product_and_series_edits_are_upload_plan_only(self):
        a=self.app
        machine=Path(self.tmp.name)/'03.HW.015350-46P01 空气压缩机.pdf'; machine.write_bytes(b'PDF-M')
        a.add_pdfs([str(machine)])
        product_iid=next(iid for iid,code in a._tree_product_nodes.items() if code=='03.HW.015350-46P01')
        series_iid=next(iid for iid,path in a._tree_product_groups.items() if path=='HW系列')
        a._apply_inline_product(a._tree_product_nodes[product_iid],'HW1.5-35(直连/水冷)')
        self.assertEqual(
            a._item_by_code('03.HW.015350-46P01')['series_product_name'],
            'HW1.5-35(直连/水冷)',
        )
        a._apply_inline_series(series_iid,'HW压缩机系列')
        self.assertEqual(
            a._item_by_code('03.HW.015350-46P01')['series_product_folder'],
            'HW压缩机系列',
        )
        self.assertFalse(hasattr(a,'_series_target_worker'))
    def test_inline_filename_edit_updates_item_and_relations(self):
        a=self.app
        parent=Path(self.tmp.name)/'01.01 parent.pdf'; parent.write_bytes(b'PDF-P')
        child=Path(self.tmp.name)/'01.01-01 child.pdf'; child.write_bytes(b'PDF-C')
        a.add_pdfs([str(parent)])
        a.add_pdfs([str(child)],parent_code='01.01')
        child_iid=next(iid for iid,code in a._tree_codes.items() if code=='01.01-01')
        with patch.object(a.tree,'bbox',return_value=(5,5,320,24)):
            a._begin_inline_edit(child_iid)
        self.assertIsNotNone(a._inline_editor)
        a._inline_editor.delete(0,'end');a._inline_editor.insert(0,'01.01-02 renamed')
        a._commit_inline_edit()
        item=next(item for item in a.items if item['code']=='01.01-02')
        self.assertEqual(item['name'],'renamed')
        self.assertEqual(
            (a.relations[0]['parent_code'],a.relations[0]['child_code']),
            ('01.01','01.01-02'),
        )
    def test_normalize_preserves_checkbox_and_requires_pdf_change_confirmation(self):
        a = self.app
        first = Path(self.tmp.name) / "01.01 First.pdf"
        second = Path(self.tmp.name) / "01.02 Second.pdf"
        first.write_bytes(b"%PDF-one")
        second.write_bytes(b"%PDF-two")
        a.add_pdfs([str(first), str(second)])
        changed = a._item_by_code("01.01")
        deselected = a._item_by_code("01.02")
        deselected["upload_sel"] = False
        old_hash = changed["sha256"]
        first.write_bytes(b"%PDF-replacement")
        with patch.object(ui.messagebox, "askyesno", return_value=False) as confirm:
            result = a.normalize_filenames(show_feedback=False)
        confirm.assert_called_once()
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(changed["sha256"], old_hash)
        self.assertFalse(deselected["upload_sel"])
        self.assertFalse(ui.preview_bundle(a.bundle)["ready"])

        with patch.object(ui.messagebox, "askyesno", return_value=True) as confirm:
            result = a.normalize_filenames(show_feedback=False)
        confirm.assert_called_once()
        self.assertEqual(result["skipped"], 0)
        self.assertNotEqual(changed["sha256"], old_hash)
        self.assertFalse(deselected["upload_sel"])
        self.assertTrue(ui.preview_bundle(a.bundle)["ready"])

    def test_recovery_retry_requires_current_batch_and_account(self):
        a = self.app
        a.add_pdfs([str(self.f)])
        a.ip_var.set("192.0.2.22")
        a.user_var.set("operator")
        item = a.items[0]
        item["status"] = "上传完成"
        settings = a._build_settings()
        current_digest = ui.content_digest(a.bundle, settings)
        other_digest = ui.content_digest(a.bundle, {**settings, "user": "other"})
        row = {"kind": "upload", "key": item["code"], "item_code": item["code"]}
        for digest in (other_digest, current_digest):
            with self.subTest(digest=digest):
                task = {"task_id": "test-task", "digest": digest}
                with (
                    patch.object(ui.journal_module, "TaskJournal") as make_journal,
                    patch.object(ui, "choose_pending_recovery_action",
                                 return_value=("no_write_retry", [(task, row)])),
                    patch.object(a, "after") as after,
                ):
                    journal = make_journal.return_value
                    journal.list_tasks_with_pending_operations.return_value = [task]
                    journal.pending_operations.return_value = [row]
                    a.recover_pending()
                    journal.resolve_operation.assert_called_once()
                    if digest == current_digest:
                        after.assert_called_once_with(0, a.start_upload)
                        self.assertEqual(item["status"], "待上传")
                    else:
                        after.assert_not_called()
                        self.assertEqual(item["status"], "上传完成")

    def test_one_click_normalize_records_fingerprint_for_changed_process_name(self):
        a=self.app
        source=Path(self.tmp.name)/'Z03.HG.010250A-11P03 空气压缩机.pdf'
        source.write_bytes(b'%PDF normalized process document')
        a.add_pdfs([str(source)])
        item=a.items[0]
        self.assertTrue(item['sha256'])
        self.assertIsNotNone(item['size'])
        a.normalize_filenames()
        self.assertEqual(item['code'],'Z03.HG.010250A-11P03')
        self.assertEqual(item['name'],'')
        self.assertEqual(item['machine'],'03.HG.010250A-11P03')
        self.assertEqual(
            ui.drawing_bundle.expected_drawing_filename(item['code'],item['name']),
            'Z03.HG.010250A-11P03.pdf',
        )
        self.assertTrue(item['sha256'])
        self.assertIsNotNone(item['size'])
        preview=ui.preview_bundle(ui.ui_logic.snapshot_manifest(a.bundle))
        self.assertTrue(preview['ready'])
        self.assertEqual(preview['fingerprint_errors'],0)
    def test_same_01_series_parent_defaults_to_own(self):
        a=self.app
        parent=Path(self.tmp.name)/'01.22.01.V202.Z3 支架包.pdf'; parent.write_bytes(b'PDF-P')
        child=Path(self.tmp.name)/'01.22.01.V202.1001 电机底座.pdf'; child.write_bytes(b'PDF-C')
        other=Path(self.tmp.name)/'01.23.01.V202.1001 另一个系列.pdf'; other.write_bytes(b'PDF-O')
        a.add_pdfs([str(parent)])
        a.add_pdfs([str(child)],parent_code='01.22.01.V202.Z3')
        a.add_pdfs([str(other)],parent_code='01.22.01.V202.Z3')
        by_pair={(rel['parent_code'],rel['child_code']):rel for rel in a.relations}
        self.assertEqual(by_pair[('01.22.01.V202.Z3','01.22.01.V202.1001')]['link_type'],'own')
        self.assertEqual(by_pair[('01.22.01.V202.Z3','01.23.01.V202.1001')]['link_type'],'borrow')
        by_code={item['code']:item for item in a.items}
        self.assertFalse(by_code['01.22.01.V202.1001']['borrow'])
        self.assertTrue(by_code['01.23.01.V202.1001']['borrow'])
    def test_manual_relation_is_borrow_only_and_survives_freeze(self):
        a=self.app
        a._add_relation('03.HE.050250W-21P04','04.99.NOT-IN-BATCH',link_type='borrow',source='manual',manual_override=True,link_only=True)
        rel=next(rel for rel in a.relations if rel['child_code']=='04.99.NOT-IN-BATCH')
        self.assertEqual(rel['link_type'],'borrow')
        self.assertTrue(rel['manual_override'])
        self.assertTrue(rel['link_only'])
        frozen=ui.ui_logic.snapshot_manifest(a.bundle)
        self.assertTrue(frozen['relations'][0]['link_only'])
    def test_context_menu_offers_batch_borrow_undo_only_for_batch_created_borrow(self):
        a=self.app
        machine_path=Path(self.tmp.name)/'03.HW.001 整机.pdf'; machine_path.write_bytes(b'PDF-M')
        batch_borrow=Path(self.tmp.name)/'04.001 本批借用.pdf'; batch_borrow.write_bytes(b'PDF-B')
        existing_borrow=Path(self.tmp.name)/'04.002 已有借用.pdf'; existing_borrow.write_bytes(b'PDF-E')
        own_parent=Path(self.tmp.name)/'01.01 父件.pdf'; own_parent.write_bytes(b'PDF-P')
        own_child=Path(self.tmp.name)/'01.01-01 归属.pdf'; own_child.write_bytes(b'PDF-C')
        a.add_pdfs([str(machine_path),str(batch_borrow),str(existing_borrow),str(own_parent),str(own_child)])
        a._add_relation('03.HW.001','04.001',link_type='borrow',manual_override=True)
        a._add_relation('03.HW.001','04.002',link_type='borrow',manual_override=True)
        for rel in a.relations:
            if rel['child_code']=='04.001':
                rel.update(created_by_batch=True,link_status='已借用',parent_id=100,child_id=200,link_id=5001,link_class_id=1037,workflow_locked=False)
            elif rel['child_code']=='04.002':
                rel.update(created_by_batch=False,link_status='已存在')
            elif rel['child_code']=='01.01-01':
                rel.update(created_by_batch=False,link_status='已归属')
        a.refresh_tree()
        class FakeMenu:
            def __init__(self,*args,**kwargs): self.labels=[]
            def add_command(self,label,command): self.labels.append(label)
            def add_separator(self): self.labels.append('---')
            def tk_popup(self,*args): pass
        by_child={rel['child_code']:iid for iid,rel in a._tree_relations.items() if rel}
        for child_code, expected in (('04.001',True),('04.002',False),('01.01-01',False)):
            fake=FakeMenu()
            with patch.object(ui.tk,'Menu',return_value=fake),patch.object(a.tree,'identify_row',return_value=by_child[child_code]):
                a._show_row_menu(SimpleNamespace(y=10,x_root=1,y_root=1))
            if expected:
                self.assertIn('撤回 CAXA 借用（仅限本批）…',fake.labels)
            else:
                self.assertNotIn('撤回 CAXA 借用（仅限本批）…',fake.labels)
if __name__=='__main__':unittest.main(verbosity=2)
