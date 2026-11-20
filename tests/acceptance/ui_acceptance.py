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
        self.assertEqual(tuple(a.duplicate_combo.cget('values')),('追加新版次','已有则复用'))
        self.assertEqual(a.duplicate_var.get(),'追加新版次')
    def test_workflow_only_button_is_available_in_action_bar(self):
        labels=[str(button.cget('text')) for button in self.app.action_buttons]
        self.assertIn('发起流程',labels)
    def test_update_button_is_integrated_and_never_auto_downloads(self):
        self.assertIn(self.app.update_button,self.app.action_buttons)
        text=str(self.app.update_button.cget('text'))
        self.assertTrue(text=='检查更新' or text.startswith('下载 v'))
        self.assertFalse(self.app._automatic_update_check_enabled())
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
        with patch.object(a.tree,'identify',return_value='cell'), \
             patch.object(a.tree,'identify_column',return_value='#8'), \
             patch.object(a.tree,'identify_row',return_value=iid):
            a._toggle_upload(SimpleNamespace(x=5,y=5))
        self.assertFalse(item['upgrade_only'])
        self.assertIn('☐',a.tree.item(iid,'values')[7])
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
    def test_series_product_field_is_per_selected_03_and_required_after_precheck(self):
        a=self.app
        m1=Path(self.tmp.name)/'03.HW.015350-46P01 空气压缩机.pdf'; m1.write_bytes(b'PDF-M1')
        m2=Path(self.tmp.name)/'03.HW.010350-11P01 空气压缩机二.pdf'; m2.write_bytes(b'PDF-M2')
        a.add_pdfs([str(m1),str(m2)])
        first=next(iid for iid,code in a._tree_codes.items() if code=='03.HW.015350-46P01')
        second=next(iid for iid,code in a._tree_codes.items() if code=='03.HW.010350-11P01')
        a.tree.selection_set(first);a._on_tree_selection_changed();a.series_product_var.set('HW1.5-35')
        a.tree.selection_set(second);a._on_tree_selection_changed();a.series_product_var.set('HW1-350')
        a.tree.selection_set(first);a._on_tree_selection_changed()
        self.assertEqual(a.series_product_var.get(),'HW1.5-35')
        a._set_series_product_required({'03.HW.015350-46P01'})
        a._select_machine_code('03.HW.015350-46P01')
        self.assertIn('必填',a.series_product_label_var.get())
        a.series_product_var.set('HW1.5-35(直连/水冷)')
        self.assertEqual(a._item_by_code('03.HW.015350-46P01')['series_product_name'],'HW1.5-35(直连/水冷)')
        self.assertEqual(a._item_by_code('03.HW.010350-11P01')['series_product_name'],'HW1-350')
    def test_product_node_context_menu_has_rename_edit(self):
        a=self.app
        machine=Path(self.tmp.name)/'03.HW.015350-46P01 空气压缩机.pdf'; machine.write_bytes(b'PDF-M')
        a.add_pdfs([str(machine)])
        product_iid=next(iid for iid,code in a._tree_product_nodes.items() if code=='03.HW.015350-46P01')
        machine_iid=next(iid for iid,code in a._tree_codes.items() if code=='03.HW.015350-46P01')
        class FakeMenu:
            def __init__(self,*args,**kwargs): self.labels=[]
            def add_command(self,label,command): self.labels.append(label)
            def add_separator(self): self.labels.append('---')
            def tk_popup(self,*args): pass
        fake=FakeMenu()
        with patch.object(ui.tk,'Menu',return_value=fake),patch.object(a.tree,'identify_row',return_value=product_iid):
            a._show_row_menu(SimpleNamespace(y=10,x_root=1,y_root=1))
        self.assertIn('修改完整信息…',fake.labels)
        self.assertIn('修改/新建计划产品名称…',fake.labels)
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
        self.assertEqual(a._series_product_code,'')
        self.assertTrue(machine_path.exists())
        self.assertTrue(child_path.exists())
        self.assertTrue(process_path.exists())
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
        a._apply_inline_product(product_iid,'HW1.5-35(直连/水冷)')
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
if __name__=='__main__':unittest.main(verbosity=2)
