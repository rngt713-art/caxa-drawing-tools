"""Distributable CAXA uploader for reviewed drawing bundles.

This entry point deliberately has no dependency on the Qwen vision pipeline.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import traceback
import uuid
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinterdnd2 import DND_FILES, TkinterDnD


APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def resource_path(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", APP_DIR)
    return os.path.join(base, relative)


import config  # noqa: E402
import drawing_bundle  # noqa: E402
import journal as journal_module  # noqa: E402
import upload_ui_logic as ui_logic  # noqa: E402
from upload_core import (  # noqa: E402
    apply_manual_borrows,
    collect_machine_targets,
    content_digest,
    precheck_with_caxa,
    preview_bundle,
    upload_bundle,
)
from recovery_verifier import (  # noqa: E402
    RecoveryEvidenceError,
    RecoveryVerifier,
    recovery_evidence_summary,
)
import update_checker  # noqa: E402
from version import __version__  # noqa: E402


KIND_LABEL = drawing_bundle.ITEM_KIND_LABELS
KIND_VALUE = {label: kind for kind, label in KIND_LABEL.items()}
LINK_TYPE_LABEL = {"own": "归属", "borrow": "借用"}

SOURCE_LABEL = {
    "drag": "本批拖拽",
    "code_prefix": "图号自动匹配",
    "manual": "手工指定",
    "z03_bom": "Z03明细表",
}
UPLOAD_COLUMN = "upload_sel"
UPGRADE_ONLY_COLUMN = "upgrade_only"


class PlaceholderEntry(ttk.Entry):
    """ttk entry with a grey hint that is never stored in the bound value."""

    def __init__(
        self,
        master,
        *,
        textvariable: tk.StringVar,
        placeholder: str,
        placeholder_color: str = "#777777",
        **kwargs,
    ):
        self._value_var = textvariable
        self._display_var = tk.StringVar(master=master)
        self._placeholder = str(placeholder or "")
        self._placeholder_color = placeholder_color
        self._normal_color = ""
        self._showing_placeholder = False
        self._syncing = False
        super().__init__(master, textvariable=self._display_var, **kwargs)
        try:
            self._normal_color = str(self.cget("foreground"))
        except tk.TclError:
            self._normal_color = ""
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self._display_var.trace_add("write", self._on_display_changed)
        self._value_var.trace_add("write", self._on_value_changed)
        self._render()

    def _has_focus(self) -> bool:
        try:
            return self.focus_get() is self
        except (AttributeError, tk.TclError):
            return False

    def _show_placeholder(self):
        if not self._placeholder or self._value_var.get():
            return
        self._syncing = True
        try:
            self._display_var.set(self._placeholder)
            self.configure(foreground=self._placeholder_color)
        finally:
            self._syncing = False
        self._showing_placeholder = True

    def _show_value(self):
        value = self._value_var.get()
        self._syncing = True
        try:
            self._display_var.set(value)
            self.configure(foreground=self._normal_color)
        finally:
            self._syncing = False
        self._showing_placeholder = False

    def _render(self):
        if self._value_var.get():
            self._show_value()
        elif self._has_focus():
            self._show_value()
        else:
            self._show_placeholder()

    def _on_focus_in(self, _event=None):
        if self._showing_placeholder:
            self._show_value()

    def _on_focus_out(self, _event=None):
        if not self._value_var.get():
            self._show_placeholder()
        else:
            self._show_value()

    def _on_display_changed(self, *_args):
        if self._syncing or self._showing_placeholder:
            return
        value = self._display_var.get()
        if self._value_var.get() != value:
            self._value_var.set(value)

    def _on_value_changed(self, *_args):
        if self._syncing:
            return
        if self._value_var.get():
            self._show_value()
        elif not self._has_focus():
            self._show_placeholder()
        else:
            self._show_value()

    def get_value(self) -> str:
        return self._value_var.get()

    @property
    def placeholder_visible(self) -> bool:
        return self._showing_placeholder


def choose_pending_recovery_action(parent, pending_tasks):
    """Show every pending operation in one window and return one batch action."""
    dialog = tk.Toplevel(parent)
    dialog.title("核实待定结果（%d 项）" % sum(
        len(rows) for _, rows in pending_tasks
    ))
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(True, True)

    frame = ttk.Frame(dialog, padding=12)
    frame.pack(fill="both", expand=True)
    ttk.Label(
        frame,
        text=(
            "所有待核实操作已合并到本窗口。可多选后回读 CAXA 或确认未写入；"
            "确认未写入后原操作允许重新执行。"
        ),
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

    columns = ("task", "kind", "subject", "stage", "status")
    tree = ttk.Treeview(
        frame,
        columns=columns,
        show="headings",
        selectmode="extended",
        height=min(14, max(5, sum(len(rows) for _, rows in pending_tasks))),
    )
    headings = {
        "task": "任务",
        "kind": "操作",
        "subject": "业务对象",
        "stage": "阶段",
        "status": "记录状态",
    }
    widths = {
        "task": 170,
        "kind": 110,
        "subject": 220,
        "stage": 150,
        "status": 90,
    }
    for column in columns:
        tree.heading(column, text=headings[column])
        tree.column(
            column,
            width=widths[column],
            minwidth=70,
            stretch=column in ("subject", "stage"),
        )
    tree.grid(row=1, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    scroll.grid(row=1, column=1, sticky="ns")
    tree.configure(yscrollcommand=scroll.set)

    detail = tk.Text(frame, height=7, wrap="word")
    detail.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
    detail.configure(state="disabled")
    frame.rowconfigure(1, weight=1)
    frame.rowconfigure(2, weight=1)
    frame.columnconfigure(0, weight=1)

    rows = []
    for task, operations in pending_tasks:
        for operation in operations:
            iid = str(len(rows))
            rows.append((task, operation))
            tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    task.get("task_id") or "",
                    ui_logic.recovery_kind_label(operation.get("kind")),
                    ui_logic.recovery_operation_subject(operation),
                    ui_logic.recovery_phase_label(
                        operation.get("kind"), operation.get("phase")
                    ),
                    operation.get("status") or "待核实",
                ),
            )

    def show_details(_event=None):
        selected = [rows[int(iid)] for iid in tree.selection()]
        lines = []
        for task, operation in selected[:8]:
            lines.append(
                "任务 %s\n%s\n错误：%s"
                % (
                    task.get("task_id") or "",
                    ui_logic.recovery_operation_text(operation),
                    operation.get("error") or "-",
                )
            )
        if len(selected) > 8:
            lines.append("其余 %d 项请缩小选择范围查看。" % (len(selected) - 8))
        detail.configure(state="normal")
        detail.delete("1.0", "end")
        detail.insert("1.0", "\n\n".join(lines))
        detail.configure(state="disabled")

    tree.bind("<<TreeviewSelect>>", show_details)
    children = tree.get_children()
    if children:
        tree.selection_set(children)
        tree.focus(children[0])
    show_details()

    result = {"action": "cancel", "selected": []}

    def choose(action, *, all_rows=False):
        selected_ids = list(tree.get_children()) if all_rows else list(tree.selection())
        if not selected_ids:
            messagebox.showwarning(
                "待核实结果", "请先选择至少一项待核实操作。", parent=dialog
            )
            return
        result["action"] = action
        result["selected"] = [rows[int(iid)] for iid in selected_ids]
        dialog.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(10, 0))
    ttk.Button(
        buttons,
        text="自动回读所选并确认成功",
        command=lambda: choose("verify"),
    ).pack(side="left", padx=(0, 6))
    ttk.Button(
        buttons,
        text="确认所选未写入",
        command=lambda: choose("no_write"),
    ).pack(side="left", padx=(0, 6))
    ttk.Button(
        buttons,
        text="全部确认未写入并重新上传",
        command=lambda: choose("no_write_retry", all_rows=True),
    ).pack(side="left", padx=(0, 6))
    ttk.Button(
        buttons,
        text="关闭",
        command=dialog.destroy,
    ).pack(side="left")

    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.update_idletasks()
    try:
        parent._center_dialog_on_parent(dialog)
    except (AttributeError, tk.TclError):
        pass
    parent.wait_window(dialog)
    return result["action"], result["selected"]


class UploadApp(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title("CAXA 图纸上传助手 v%s" % __version__)
        self.geometry("1240x780")
        self.cfg = config.load()
        self.manifest_path = None
        self.bundle = None
        self.items = []
        self.manual_borrows = []
        self.relations = []
        self.events = queue.Queue()
        self.busy = False
        self._last_identity = None
        self._active_request = 0
        self._last_result = None
        self._drag_source_row = ""
        self._drag_source_rows = ()
        self._drag_started = False
        self._drag_origin = None
        self._drag_target_row = ""
        self._drag_ghost = None
        self._inline_editor = None
        self._inline_target = ""
        self._inline_mode = ""
        self._tree_items = {}
        self._tree_relations = {}
        self._tree_codes = {}
        self._tree_product_groups = {}
        self._tree_product_nodes = {}
        self._series_product_required_codes: set[str] = set()
        self._batch_id = ""
        self.update_state = None
        self._available_update = None
        self._pending_update = None
        self._update_busy = False
        self._update_busy_label = ""
        self._update_after_id = None
        self._build_ui()
        self._apply_window_icon()
        self._init_update_state()
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._on_drop)
        self._poll_after_id = self.after(150, self._poll_events)

    def _apply_window_icon(self):
        icon = resource_path(os.path.join("assets", "app-icon.ico"))
        try:
            self.iconbitmap(icon)
            return
        except (OSError, tk.TclError):
            pass
        try:
            image = tk.PhotoImage(file=resource_path(os.path.join("assets", "app-icon.png")))
            self.iconphoto(True, image.subsample(8), image.subsample(16))
        except (OSError, tk.TclError):
            self.log("窗口图标加载失败：%s" % icon)

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Workspace.TFrame", background="#edf2f8")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("Panel.TLabel", background="#ffffff", foreground="#182d44")
        style.configure(
            "Title.TLabel", background="#ffffff", foreground="#142c49",
            font=("Segoe UI", 16, "bold"),
        )
        style.configure("Source.TLabel", background="#ffffff", foreground="#495e74")
        style.configure(
            "Toolbar.TButton", padding=(10, 6), foreground="#182d44",
            background="#f2f6fa", bordercolor="#c5d2df", lightcolor="#f2f6fa",
            darkcolor="#f2f6fa",
        )
        style.map(
            "Toolbar.TButton",
            background=[("disabled", "#edf0f4"), ("pressed", "#d7e6f1"), ("active", "#e3eef6")],
            foreground=[("disabled", "#52677d")],
        )
        style.configure(
            "Upload.TButton", padding=(14, 6), foreground="#ffffff",
            background="#00668a", bordercolor="#00668a", lightcolor="#00668a",
            darkcolor="#00668a",
        )
        style.map(
            "Upload.TButton",
            background=[("disabled", "#d8e3ed"), ("pressed", "#004a64"), ("active", "#005775")],
            foreground=[("disabled", "#52677d"), ("!disabled", "#ffffff")],
        )
        style.configure("Panel.TCheckbutton", background="#ffffff")
        style.configure(
            "Drawings.Treeview", background="#ffffff", fieldbackground="#ffffff",
            foreground="#182d44", rowheight=28,
        )
        style.configure(
            "Drawings.Treeview.Heading", background="#e6edf5", foreground="#182d44",
            padding=(6, 5), bordercolor="#c5d2df", lightcolor="#e6edf5", darkcolor="#e6edf5",
        )
        style.map(
            "Drawings.Treeview",
            background=[("selected", "#cce7f5")],
            foreground=[("selected", "#122d46")],
        )

        workspace = ttk.Frame(self, padding=12, style="Workspace.TFrame")
        workspace.pack(fill="both", expand=True)
        self.top_frame = ttk.Frame(workspace, style="Panel.TFrame")
        self.top_frame.pack(fill="x")
        self.action_buttons = []

        header = ttk.Frame(self.top_frame, style="Panel.TFrame", padding=(14, 12, 14, 4))
        header.pack(fill="x")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="图纸上传", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.bundle_var = tk.StringVar(value="未导入图纸")
        source = ttk.Label(header, textvariable=self.bundle_var, style="Source.TLabel", width=1)
        source.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(3, 0))
        source.bind("<Configure>", lambda event: source.configure(wraplength=event.width))
        self.settings_button = ttk.Button(
            header, style="Toolbar.TButton", command=self._toggle_settings
        )
        self.settings_button.grid(row=0, column=1, padx=(0, 6))
        self.update_button = ttk.Button(
            header, text="检查更新", style="Toolbar.TButton", command=self.on_update_button
        )
        self.update_button.grid(row=0, column=2)
        self.action_buttons.append(self.update_button)

        self.toolbar = ttk.Frame(self.top_frame, style="Panel.TFrame", padding=(14, 8, 14, 12))
        self.toolbar.pack(fill="x")
        for text, command in (
            ("打开上传包…", self.open_bundle),
            ("打开上传包目录…", self.open_bundle_dir),
            ("添加图纸…", self.add_pdfs),
            ("添加文件夹…", self.add_pdf_folder),
        ):
            button = ttk.Button(self.toolbar, text=text, command=command, style="Toolbar.TButton")
            button.pack(side="left", padx=(0, 6))
            self.action_buttons.append(button)

        self.upload_button = ttk.Button(
            self.toolbar, text="开始上传", command=self.start_upload, style="Upload.TButton"
        )
        self.upload_button.pack(side="right")
        self.action_buttons.append(self.upload_button)
        precheck = ttk.Button(
            self.toolbar, text="规范并预检", command=self.normalize_and_precheck,
            style="Toolbar.TButton",
        )
        precheck.pack(side="right", padx=(8, 8))
        self.action_buttons.append(precheck)

        self._build_connection_settings()
        if not self.ip_var.get().strip() or not self.user_var.get().strip():
            self._toggle_settings()

        footer = ttk.Frame(workspace, style="Panel.TFrame")
        footer.pack(side="bottom", fill="x")
        self._build_status_panel(footer)

        work_area = ttk.Frame(workspace, style="Panel.TFrame", padding=12)
        work_area.pack(fill="both", expand=True, pady=(10, 8))
        actions = ttk.Frame(work_area, style="Panel.TFrame")
        actions.pack(fill="x", pady=(0, 8))
        ttk.Label(actions, text="上传清单", style="Title.TLabel").pack(side="left", padx=(0, 12))
        for text, command in (
            ("借用已有零件", self.add_relation_dialog),
            ("切换归属 / 借用", self.toggle_selected_relation_type),
            ("移除计划关系", self.delete_selected_relation),
        ):
            button = ttk.Button(actions, text=text, command=command)
            button.pack(side="left", padx=(0, 6))
            self.action_buttons.append(button)
        for text, command in (
            ("核实待定结果", self.recover_pending),
            ("发起审核打印", self.start_workflow_only),
        ):
            button = ttk.Button(actions, text=text, command=command)
            button.pack(side="right", padx=(6, 0))
            self.action_buttons.append(button)

        self._build_drawing_tree(work_area)
        self.update_idletasks()
        min_width = max(900, self.toolbar.winfo_reqwidth() + 24, actions.winfo_reqwidth() + 48)
        min_height = max(540, round(405 * float(self.tk.call("tk", "scaling"))))
        self.minsize(min_width, min_height)
        self.dnd_bind("<<DropEnter>>", self._on_drop_hover, add="+")
        self.dnd_bind("<<DropPosition>>", self._on_drop_hover, add="+")
        self.dnd_bind("<<DropLeave>>", lambda _event: self._set_drag_target(""), add="+")

    def _build_connection_settings(self):
        settings = ttk.Frame(self.top_frame, style="Panel.TFrame", padding=(14, 2, 14, 12))
        self.settings_frame = settings
        self.settings_button.configure(text="展开连接与流程设置")

        self.ip_var = tk.StringVar(value=str(self.cfg.get("caxa_server_ip") or ""))
        self.port_var = tk.StringVar(value=str(self.cfg.get("caxa_server_port") or "8900"))
        self.user_var = tk.StringVar(value=str(self.cfg.get("caxa_user") or ""))
        self.password_var = tk.StringVar(value=config.get_password(self.cfg))
        self.performer_var = tk.StringVar(value=str(self.cfg.get("workflow_performer") or ""))
        for column, (label, variable, mask) in enumerate(
            (
                ("CAXA 服务器地址", self.ip_var, ""),
                ("端口", self.port_var, ""),
                ("用户名", self.user_var, ""),
                ("密码（可留空）", self.password_var, "*"),
                ("审核人", self.performer_var, ""),
            )
        ):
            settings.columnconfigure(column, weight=1, uniform="settings")
            ttk.Label(settings, text=label, style="Panel.TLabel").grid(
                row=0, column=column, sticky="w", padx=(0, 10)
            )
            ttk.Entry(settings, textvariable=variable, show=mask, width=12).grid(
                row=1, column=column, sticky="ew", padx=(0, 10), pady=(3, 8)
            )

        self.duplicate_var = tk.StringVar(
            value=ui_logic.duplicate_policy_label(self.cfg.get("duplicate_policy"))
        )
        ttk.Label(settings, text="重复图号处理", style="Panel.TLabel").grid(
            row=2, column=0, sticky="w"
        )
        self.duplicate_combo = ttk.Combobox(
            settings, textvariable=self.duplicate_var, state="readonly", width=12,
            values=tuple(ui_logic.DUPLICATE_POLICY_LABELS.values()),
        )
        self.duplicate_combo.grid(row=2, column=1, sticky="ew", padx=(0, 10))
        ttk.Label(
            settings, style="Source.TLabel",
            text="升版会为已有图纸追加版次。复用会沿用已有图纸。",
        ).grid(row=3, column=0, columnspan=5, sticky="w", pady=(5, 0))

        self.workflow_var = tk.BooleanVar(
            value=bool(self.cfg.get("start_workflow_after_upload", False))
        )
        ttk.Checkbutton(
            settings, text="上传成功后发起“审核打印”流程", variable=self.workflow_var,
            style="Panel.TCheckbutton",
        ).grid(row=2, column=2, columnspan=3, sticky="w")

    def _toggle_settings(self):
        if self.settings_frame.winfo_manager():
            self.settings_frame.pack_forget()
            self.settings_button.configure(text="展开连接与流程设置")
        else:
            self.settings_frame.pack(fill="x", after=self.toolbar)
            self.settings_button.configure(text="收起连接与流程设置")

    def _build_drawing_tree(self, parent):
        columns = (
            "source", "page", "kind", "machine", "relation", "filename_state",
            "upload_sel", "upgrade_only", "status", "note",
        )
        tree_frame = ttk.Frame(parent, style="Panel.TFrame")
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="tree headings", height=12,
            displaycolumns=(
                "upload_sel", "upgrade_only", "relation", "filename_state", "status",
                "note", "kind", "machine", "source", "page",
            ),
            style="Drawings.Treeview",
        )
        for column, title, width in (
            ("#0", "图纸（图号 名称）", 292),
            ("source", "来源文件", 160),
            ("page", "页", 44),
            ("kind", "文档类型", 112),
            ("machine", "所属图号", 140),
            ("relation", "关系", 145),
            ("filename_state", "命名", 68),
            ("upload_sel", "上传", 50),
            ("upgrade_only", "借用处理", 102),
            ("status", "状态", 128),
            ("note", "结果 / 备注", 230),
        ):
            self.tree.heading(column, text=title, anchor="w")
            self.tree.column(column, width=width, minwidth=min(width, 100), stretch=False)
        self.tree.tag_configure("invalid", foreground="#a51232")
        self.tree.tag_configure("drop_target", background="#d4effb")
        self.tree.tag_configure("product_group", font=("Segoe UI", 9, "bold"))
        y_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        self.tree.bind("<Button-1>", self._toggle_upload)
        self.tree.bind("<ButtonPress-1>", self._begin_row_drag, add="+")
        self.tree.bind("<B1-Motion>", self._begin_row_drag_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._finish_row_drag, add="+")
        self.tree.bind("<Button-3>", self._show_row_menu)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        ttk.Label(
            parent, style="Source.TLabel",
            text=(
                "拖入 PDF 或 PDF 文件夹 · 图纸命名：图号 名称.pdf｜工艺文档：图号.pdf"
                " · 双击系列产品名称编辑，回车保存"
            ),
        ).pack(anchor="w", pady=(6, 0))

    def _build_status_panel(self, parent):
        status = ttk.Frame(parent, padding=(12, 6), style="Panel.TFrame")
        status.pack(fill="x")
        status.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="就绪")
        summary = ttk.Label(status, textvariable=self.status_var, style="Panel.TLabel", width=1)
        summary.grid(row=0, column=0, sticky="ew")
        summary.bind("<Configure>", lambda event: summary.configure(wraplength=event.width))
        self.log_button = ttk.Button(status, text="展开日志", command=self._toggle_log)
        self.log_button.grid(row=0, column=1, padx=(8, 0))

        self.log_frame = ttk.Frame(parent, padding=(12, 0, 12, 8), style="Panel.TFrame")
        self.log_text = tk.Text(
            self.log_frame, height=7, state="disabled", wrap="word", borderwidth=0,
            padx=8, pady=5, background="#f1f5fa", foreground="#243b55",
            font="TkFixedFont",
        )
        scroll = ttk.Scrollbar(self.log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

    def _toggle_log(self):
        if self.log_frame.winfo_manager():
            self.log_frame.pack_forget()
            self.log_button.configure(text="展开日志")
        else:
            self.log_frame.pack(fill="x")
            self.log_text.see("end")
            self.log_button.configure(text="收起日志")

    def _drawing_column_at(self, x: int) -> str:
        display_column = self.tree.identify_column(x)
        if not display_column:
            return ""
        return self.tree.column(display_column, "id")

    def log(self, message: str):
        self.events.put(("log", message))

    def _append_ui_log(self, message: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", str(message).rstrip() + "\n")
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 5000:
            self.log_text.delete("1.0", "%d.0" % (line_count - 5000 + 1))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_ui_log(payload)
                elif kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "update_checked":
                    self._handle_update_checked(payload)
                elif kind == "update_error":
                    self._handle_update_error(payload)
                elif kind == "update_downloaded":
                    self._handle_update_downloaded(payload)
                elif kind == "update_download_error":
                    self._handle_update_download_error(payload)
                elif kind == "update_relaunch_ready":
                    self._handle_update_relaunch_ready(payload)
                elif kind == "update_relaunch_error":
                    self._handle_update_relaunch_error(payload)
                elif kind == "refresh":
                    self.refresh_tree()
                elif kind == "precheck_done":
                    snapshot, info = payload
                    ui_logic.apply_precheck_items(self.items, snapshot)
                    ui_logic.apply_relation_result(
                        self.relations, snapshot
                    )
                    self.set_busy(False)
                    self.refresh_tree()
                    products = list(info.get("machine_products") or [])
                    existing_products = sum(1 for row in products if row["exists"])
                    missing_products = len(products) - existing_products
                    missing_parts = list(info.get("missing_product_parts") or [])
                    missing_process_parts = list(
                        info.get("missing_process_parts") or []
                    )
                    process_parts_will_create = list(
                        info.get("process_parts_will_create") or []
                    )
                    duplicate_process_parts = list(
                        info.get("duplicate_process_parts") or []
                    )
                    missing_folders = [
                        row for row in products
                        if not row.get("folder_exists", True)
                    ]
                    existing_originals = list(
                        info.get("existing_original_items") or []
                    )
                    only_upgrade_count = int(
                        info.get("only_upgrade_count") or 0
                    )
                    borrow_after_upgrade_count = int(
                        info.get("borrow_after_upgrade_count") or 0
                    )
                    missing_borrow_positions = list(
                        info.get("missing_borrow_positions") or []
                    )
                    duplicate_parts = list(
                        info.get("duplicate_existing_parts") or []
                    )
                    missing_names = [
                        row for row in products
                        if row.get("requires_product_name", not row["exists"])
                        and not str(row.get("requested_name") or "").strip()
                    ]
                    self._set_series_product_required(
                        {
                            drawing_bundle.normalize_code(row.get("code"))
                            for row in products
                            if row.get("requires_product_name", not row["exists"])
                        }
                    )
                    if missing_names:
                        self._select_machine_code(missing_names[0]["code"])
                    product_text = ""
                    if products:
                        product_text = (
                            "；03整机产品已有 %d 个，待新建 %d 个，"
                            "缺少整机零件 %d 个，缺少系列目录 %d 个"
                            % (
                                existing_products,
                                missing_products,
                                len(missing_parts),
                                len(missing_folders),
                            )
                        )
                    if missing_process_parts:
                        product_text += "，缺少 02 半成品零件 %d 个" % len(
                            missing_process_parts
                        )
                    if process_parts_will_create:
                        product_text += "；02半成品叶子待创建 %d 个" % len(
                            process_parts_will_create
                        )
                    self.status_var.set(
                        "预检完成：已有原件 %d 个（不借用 %d，按计划借用 %d），"
                        "共 %d 条关系（归属 %d，借用 %d）%s"
                        % (
                            len(existing_originals),
                            only_upgrade_count,
                            borrow_after_upgrade_count,
                            info["checked_relations"],
                            info["own_count"],
                            info["borrow_count"],
                            product_text,
                        )
                    )
                    product_detail = ""
                    if missing_products:
                        product_detail = (
                            "\n\n有 %d 个03整机所属产品尚不存在。"
                            "请双击上传清单中对应的系列产品行填写各自的"
                            "系列产品名称；"
                            "上传时会以整机图号为产品代号创建。"
                            % missing_products
                        )
                    part_detail = ""
                    if missing_parts:
                        part_detail = (
                            "\n\n以下 03 整机的产品已存在，上传时会自动创建缺少的整机零件：\n"
                            + "\n".join(
                                str(row.get("code") or "")
                                for row in missing_parts[:20]
                            )
                        )
                    process_detail = ""
                    if missing_process_parts:
                        process_detail = (
                            "\n\n以下02半成品的既有分组或根产品不满足规则，"
                            "上传已阻止。助手只允许创建已存在 02.x 分组下的"
                            "叶子层级：\n"
                            + "\n".join(
                                "%s（%s）"
                                % (
                                    row.get("code") or "",
                                    row.get("blocked_reason") or "不能自动建立",
                                )
                                for row in missing_process_parts[:20]
                            )
                        )
                    process_create_detail = ""
                    if process_parts_will_create:
                        process_create_detail = (
                            "\n\n以下 02 半成品零件会自动建在现有的最近上级零件下：\n"
                            + "\n".join(
                                "%s（上级 %s）"
                                % (
                                    row.get("code") or "",
                                    row.get("parent_code") or "",
                                )
                                for row in process_parts_will_create[:20]
                            )
                        )
                    folder_detail = ""
                    if missing_folders:
                        folder_detail = (
                            "\n\n以下系列产品目录在 CAXA 中不存在，"
                            "上传已阻止；请先在 CAXA 设计树中建立或修正：\n"
                            + "\n".join(
                                "%s → %s"
                                % (row.get("code"), row.get("folder") or "")
                                for row in missing_folders[:20]
                            )
                        )
                    missing_links = list(info.get("missing_link_children") or [])
                    link_detail = ""
                    if missing_links:
                        link_detail = (
                            "\n\n以下手工借用子项在 CAXA 中不存在，将跳过并报错：\n"
                            + "\n".join(missing_links[:20])
                        )
                    original_detail = ""
                    if existing_originals:
                        original_detail = (
                            "\n\n以下图号在 CAXA 中已有原件。"
                            "预检已将计划归属改成借用，可在「借用处理」列调整：\n"
                            + "\n".join(
                                str(row.get("code") or "")
                                for row in existing_originals[:20]
                            )
                        )
                    borrow_position_detail = ""
                    if missing_borrow_positions:
                        borrow_position_detail = (
                            "\n\n以下已有原件选择了「按计划借用」，"
                            "但没有设置借用父级，上传已阻止：\n"
                            + "\n".join(missing_borrow_positions[:20])
                        )
                    duplicate_part_detail = ""
                    if duplicate_parts:
                        duplicate_part_detail = (
                            "\n\n以下图号的原件查询结果不一致。"
                            "请在 CAXA 设计树中检查重复显示的挂接及借用属性：\n"
                            + "\n".join(duplicate_parts[:20])
                        )
                    if duplicate_process_parts:
                        duplicate_part_detail += (
                            "\n\n以下 02 半成品或其上级的原件无法确认，或存在多个归属挂接。"
                            "请在 CAXA 设计树中检查后重试：\n"
                            + "\n".join(
                                "%s（上级 %s）"
                                % (
                                    row.get("code") or "",
                                    row.get("parent_code") or "",
                                )
                                for row in duplicate_process_parts[:20]
                            )
                        )
                    message = (
                        "共 %d 条关系：归属子件 %d 条，借用 %d 条。\n"
                        "可在层级树中双击关系行进行人工勘误切换。"
                        + product_detail
                        + part_detail
                        + process_detail
                        + process_create_detail
                        + folder_detail
                        + link_detail
                        + original_detail
                        + borrow_position_detail
                        + duplicate_part_detail
                    ) % (
                        info["checked_relations"],
                        info["own_count"],
                        info["borrow_count"],
                    )
                    if (
                        missing_names
                        or missing_parts
                        or missing_process_parts
                        or missing_links
                        or missing_folders
                        or missing_borrow_positions
                        or duplicate_parts
                    ):
                        messagebox.showwarning("CAXA 预检需要处理", message)
                    else:
                        messagebox.showinfo("CAXA 设计树预检完成", message)
                elif kind == "upload_done":
                    snapshot, result, task_settings, workflow_only = payload
                    identity_now = None
                    try:
                        identity_now = ui_logic.identity_key(self._build_settings())
                    except Exception:
                        identity_now = None
                    same_identity = (
                        identity_now is None
                        or identity_now == ui_logic.identity_key(task_settings)
                    )
                    if same_identity:
                        ui_logic.apply_upload_result(self.items, snapshot)
                        ui_logic.apply_relation_result(self.relations, snapshot)
                    else:
                        ui_logic.invalidate_successes(
                            self.items, "上传期间账号/服务器已变更，结果未合并"
                        )
                    self._last_result = result
                    self.set_busy(False)
                    self.refresh_tree()
                    summary = ui_logic.summary_message(result)
                    if result.get("ok"):
                        self.status_var.set("完成")
                        messagebox.showinfo("上传结果", summary)
                    else:
                        self.status_var.set("上传未全部成功")
                        messagebox.showwarning("上传结果（需人工确认）", summary)
                    if result.get("duplicate") and not workflow_only:
                        if messagebox.askyesno(
                            "相同批次已完成",
                            summary + "\n\n是否作为“新任务”重新提交一次？\n"
                            "（内容或账号变化会自动视为新任务；重传会新建版次）",
                        ):
                            self.start_upload(force_new_task=True)
                elif kind == "manual_done":
                    snapshot, result = payload
                    ui_logic.apply_relation_result(self.relations, snapshot)
                    self.set_busy(False)
                    self.refresh_tree()
                    self.status_var.set(
                        "手动关系：完成 %d，已存在 %d，缺失 %d，失败 %d"
                        % (
                            result["borrowed"],
                            result["skipped"],
                            result["missing"],
                            result["failed"],
                        )
                    )
                    messagebox.showinfo(
                        "手动借用结果",
                        "已建立/确认 %d 条，已存在 %d 条，缺对象 %d 条，失败 %d 条"
                        % (
                            result["borrowed"],
                            result["skipped"],
                            result["missing"],
                            result["failed"],
                        ),
                    )
                elif kind == "recovery_done":
                    successes, failures = payload
                    self.set_busy(False)
                    lines = []
                    if successes:
                        lines.append(
                            "已自动回读确认成功 %d 项：" % len(successes)
                        )
                        lines.extend("· " + value for value in successes[:12])
                    if failures:
                        lines.append(
                            "仍保持待核实 %d 项：" % len(failures)
                        )
                        lines.extend("· " + value for value in failures[:12])
                        if len(failures) > 12:
                            lines.append(
                                "· 其余 %d 项请缩小选择范围处理"
                                % (len(failures) - 12)
                            )
                        messagebox.showwarning(
                            "待核实结果", "\n".join(lines)
                        )
                    elif successes:
                        messagebox.showinfo(
                            "核实成功", "\n".join(lines)
                        )
                    self.status_var.set(
                        "待核实处理完成：成功 %d 项，未确认 %d 项"
                        % (len(successes), len(failures))
                    )
                elif kind == "recovery_error":
                    self.set_busy(False)
                    self.status_var.set("待核实回读失败")
                    messagebox.showerror("待核实结果", str(payload))
                elif kind == "done":
                    self.set_busy(False)
                    self.status_var.set("完成")
                    messagebox.showinfo("完成", str(payload))
                elif kind == "error":
                    self.set_busy(False)
                    self.status_var.set("失败")
                    messagebox.showerror("错误", str(payload))
                elif kind == "task_error":
                    self.set_busy(False)
                    self.refresh_tree()
                    self.status_var.set("失败")
                    messagebox.showerror("任务失败", str(payload))
        except queue.Empty:
            pass
        except Exception as exc:
            traceback.print_exc()
            try:
                self._append_ui_log(
                    "界面事件处理异常：%s: %s\n"
                    % (type(exc).__name__, exc),
                )
            except tk.TclError:
                pass
        finally:
            self._poll_after_id = self.after(150, self._poll_events)

    def destroy(self):
        if self._update_after_id is not None:
            try:
                self.after_cancel(self._update_after_id)
            except Exception:
                pass
            self._update_after_id = None
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:
                pass
            self._poll_after_id = None
        super().destroy()

    def set_busy(self, busy: bool):
        """Freeze every mutating entry point for the duration of one task."""
        self.busy = bool(busy)
        state = "disabled" if busy else "normal"
        for button in getattr(self, "action_buttons", []):
            try:
                button.configure(state=state)
            except tk.TclError:
                pass
        # Account/server/widget settings are frozen too: a task must not be
        # shown as successful under an account the user switched to mid-run.
        for widget, normal_state in self._config_widget_states().items():
            try:
                widget.configure(state=normal_state if not busy else "disabled")
            except tk.TclError:
                pass
        try:
            self.tree.configure(selectmode="none" if busy else "extended")
        except tk.TclError:
            pass
        self._refresh_update_button()
        if not busy:
            self._maybe_start_pending_update()

    def _blocked_by_busy(self) -> bool:
        if self.busy:
            self.status_var.set("当前任务进行中，暂时不能修改清单或再次提交")
            return True
        return False

    def _blocked_by_update(self) -> bool:
        if self._update_busy or self._pending_update is not None:
            self.status_var.set("正在下载或安装更新，暂时不能开始 CAXA 写入")
            return True
        return False

    def _config_widget_states(self) -> dict:
        """Editable account/widgets in the top form, with their normal state."""
        if getattr(self, "_config_states_cache", None) is not None:
            return self._config_states_cache
        states = {}

        def walk(widget):
            for child in widget.winfo_children():
                if child in getattr(self, "action_buttons", []):
                    continue
                try:
                    cls = child.winfo_class()
                except tk.TclError:
                    continue
                if cls in ("TEntry", "TCombobox", "TCheckbutton", "Entry", "Checkbutton"):
                    try:
                        states[child] = str(child.cget("state")) or "normal"
                    except tk.TclError:
                        states[child] = "normal"
                walk(child)

        walk(self.top_frame)
        self._config_states_cache = states
        return states

    def _init_update_state(self):
        self.update_state = update_checker.UpdateStateStore()
        checked_today = self.update_state.checked_today()
        self._available_update = (
            self.update_state.cached_update(__version__) if checked_today else None
        )
        self._refresh_update_button()
        if self._automatic_update_check_enabled() and not checked_today:
            self._update_after_id = self.after(700, self._start_daily_update_check)

    def _ensure_batch_id(self) -> str:
        if self.bundle is None:
            return ""
        current = str(self.bundle.get("batch_id") or "").strip()
        if current:
            self._batch_id = current
            return current
        self._batch_id = "B%s-%s" % (
            time.strftime("%Y%m%d%H%M%S"),
            uuid.uuid4().hex[:12],
        )
        self.bundle["batch_id"] = self._batch_id
        return self._batch_id

    def _automatic_update_check_enabled(self) -> bool:
        disabled = os.environ.get("CAXA_DISABLE_UPDATE_CHECK", "").strip()
        if disabled.lower() in ("1", "true", "yes", "on"):
            return False
        forced = os.environ.get("CAXA_ENABLE_UPDATE_CHECK", "").strip()
        return bool(getattr(sys, "frozen", False)) or forced.lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    def _start_daily_update_check(self):
        self._update_after_id = None
        if self.update_state.checked_today():
            return
        if self.busy or self._update_busy:
            self._update_after_id = self.after(700, self._start_daily_update_check)
            return
        self._start_update_check(manual=False)

    def _refresh_update_button(self):
        if not hasattr(self, "update_button"):
            return
        if self._update_busy:
            text = self._update_busy_label or "检查中…"
            state = "disabled"
        elif self._pending_update is not None:
            text = "更新待安装"
            state = "disabled"
        elif self.busy:
            text = "检查更新"
            state = "disabled"
        elif self._available_update is not None:
            text = "下载 v%s" % self._available_update.latest_version
            state = "normal"
        else:
            text = "检查更新"
            state = "normal"
        try:
            self.update_button.configure(text=text, state=state)
        except tk.TclError:
            pass

    def on_update_button(self):
        if self.busy:
            self.status_var.set("当前任务进行中，暂时不能检查或下载更新")
            return
        if self._update_busy:
            return
        if self._available_update is not None:
            self._start_update_download()
            return
        self._start_update_check(manual=True)

    def _start_update_check(self, *, manual: bool):
        if self._update_busy or self.busy:
            return
        self._update_busy = True
        self._update_busy_label = "检查中…"
        self._refresh_update_button()
        self.status_var.set("正在检查 GitHub 最新版本…")
        threading.Thread(
            target=self._check_update_worker,
            args=(manual,),
            daemon=True,
        ).start()

    def _check_update_worker(self, manual: bool):
        try:
            info = update_checker.check_for_update(__version__)
            try:
                self.update_state.mark_attempt(success=True, info=info)
            except Exception:
                pass
            self.events.put(("update_checked", (info, manual)))
        except Exception as exc:
            try:
                self.update_state.mark_attempt(
                    success=False,
                    error="%s: %s" % (type(exc).__name__, exc),
                )
            except Exception:
                pass
            self.events.put(("update_error", (exc, manual)))

    def _handle_update_checked(self, payload):
        info, manual = payload
        self._update_busy = False
        self._update_busy_label = ""
        if info.available:
            self._available_update = info
            message = (
                "发现新版本 v%s，点击“下载 v%s”从 GitHub 获取。"
                % (info.latest_version, info.latest_version)
            )
            self.log(message)
            self.status_var.set(message)
            if manual:
                messagebox.showinfo(
                    "发现新版本",
                    "%s\n\n当前版本：v%s\n最新版本：v%s"
                    % (message, __version__, info.latest_version),
                )
        else:
            self._available_update = None
            message = "当前已是最新版本 v%s" % __version__
            self.log(message)
            self.status_var.set(message)
            if manual:
                messagebox.showinfo("检查更新", message)
        self._refresh_update_button()

    def _handle_update_error(self, payload):
        exc, manual = payload
        self._update_busy = False
        self._update_busy_label = ""
        message = "检查更新失败：%s: %s" % (type(exc).__name__, exc)
        self.log(message)
        self.status_var.set("检查更新失败")
        if manual:
            messagebox.showwarning("检查更新", message)
        self._refresh_update_button()

    def _start_update_download(self):
        if self._update_busy or self.busy or self._available_update is None:
            return
        info = self._available_update
        self._update_busy = True
        self._update_busy_label = "下载中…"
        self._refresh_update_button()
        self.status_var.set("正在从 GitHub 下载 v%s…" % info.latest_version)
        threading.Thread(
            target=self._download_update_worker,
            args=(info,),
            daemon=True,
        ).start()

    def _download_update_worker(self, info):
        try:
            path = update_checker.download_update(info)
            self.events.put(("update_downloaded", (info, path)))
        except Exception as exc:
            self.events.put(("update_download_error", (info, exc)))

    def _handle_update_downloaded(self, payload):
        info, path = payload
        if self.busy:
            self._pending_update = (info, Path(path))
            self._update_busy = False
            self._update_busy_label = ""
            message = (
                "新版本 v%s 已下载并校验；当前任务完成后再自动覆盖并重启。"
                % info.latest_version
            )
            self.log(message)
            self.status_var.set(message)
            self._refresh_update_button()
            return
        self._update_busy_label = "启动新版…"
        self._refresh_update_button()
        self.log(
            "新版本 v%s 已下载并通过 SHA-256 校验，正在启动新版：%s"
            % (info.latest_version, path)
        )
        self.status_var.set("更新已下载，正在启动新版…")
        threading.Thread(
            target=self._relaunch_update_worker,
            args=(info, Path(path)),
            daemon=True,
        ).start()

    def _maybe_start_pending_update(self):
        if self._pending_update is None or self.busy or self._update_busy:
            return
        info, path = self._pending_update
        self._pending_update = None
        self._update_busy = True
        self._update_busy_label = "启动新版…"
        self._refresh_update_button()
        self.status_var.set("当前任务已完成，正在启动新版…")
        threading.Thread(
            target=self._relaunch_update_worker,
            args=(info, path),
            daemon=True,
        ).start()

    def _handle_update_download_error(self, payload):
        info, exc = payload
        self._update_busy = False
        self._update_busy_label = ""
        self._available_update = None
        try:
            self.update_state.clear_cached_update()
        except OSError as cache_error:
            self.log("无法清除更新缓存：%s" % cache_error)
        self._refresh_update_button()
        message = "下载 v%s 失败：%s: %s" % (
            info.latest_version,
            type(exc).__name__,
            exc,
        )
        self.log(message)
        self.status_var.set("更新下载失败")
        messagebox.showerror("更新下载失败", message + "\n请点击“检查更新”后重试。")

    def _relaunch_update_worker(self, info, path: Path):
        try:
            install_target = (
                Path(sys.executable)
                if getattr(sys, "frozen", False)
                else None
            )
            update_checker.launch_update(
                path,
                install_target=install_target,
                parent_pid=os.getpid(),
            )
        except Exception as exc:
            self.events.put(("update_relaunch_error", (info, exc)))
            return
        self.events.put(("update_relaunch_ready", (info, path)))

    def _handle_update_relaunch_ready(self, payload):
        _info, path = payload
        # Keep CAXA writes blocked until the old process has exited.
        self.log("新版本已启动，正在关闭当前版本：%s" % path)
        self.status_var.set("新版本已启动，正在关闭当前版本…")
        self.after(250, self.destroy)

    def _handle_update_relaunch_error(self, payload):
        info, exc = payload
        self._update_busy = False
        self._update_busy_label = ""
        self._refresh_update_button()
        message = "新版本 v%s 已下载，但无法启动：%s" % (
            info.latest_version,
            exc,
        )
        self.log(message)
        self.status_var.set("新版启动失败")
        messagebox.showerror("更新启动失败", message)

    def _on_drop(self, event):
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        target_code = ""
        target_label = ""
        try:
            y = int(event.y_root) - self.tree.winfo_rooty()
            row = self.tree.identify_row(y)
            if row:
                target_item = self._tree_items.get(row)
                candidate = self._tree_codes.get(row, "")
                if drawing_bundle.is_valid_code(candidate):
                    target_code = candidate
                    target_label = " ".join(
                        value
                        for value in (
                            candidate,
                            (target_item or {}).get("name", ""),
                        )
                        if value
                    )
        except Exception:
            target_code = ""
        self._set_drag_target("")
        self.add_pdfs(paths, parent_code=target_code)
        if target_code:
            self.log(
                "拖入的图纸作为 %s 的次级条目" % target_label
            )

    def _on_drop_hover(self, event):
        try:
            y = int(event.y_root) - self.tree.winfo_rooty()
            row = self.tree.identify_row(y)
            if row and drawing_bundle.is_valid_code(self._tree_codes.get(row, "")):
                self._set_drag_target(row)
            else:
                self._set_drag_target("")
        except Exception:
            self._set_drag_target("")

    def add_pdf_folder(self):
        if self._blocked_by_busy():
            return
        path = filedialog.askdirectory(title="选择包含 PDF 的文件夹")
        if path:
            self.add_pdfs([path])

    def add_pdfs(self, paths=None, parent_code=""):
        if self._blocked_by_busy():
            return
        if paths is None:
            paths = filedialog.askopenfilenames(
                title="选择已拆分并重命名的 PDF",
                filetypes=[("PDF", "*.pdf")],
            )
        if not paths:
            return
        collection = ui_logic.collect_pdf_paths(paths)
        pdf_paths = collection["files"]
        ignored_count = int(collection["ignored"] or 0)
        for error in collection["errors"]:
            self.log("文件夹读取失败：%s" % error)
        if not pdf_paths:
            message = "未找到 PDF 文件"
            if ignored_count:
                message += "；已忽略 %d 个文件夹或非 PDF 文件" % ignored_count
            if collection["errors"]:
                message += "；读取失败 %d 个路径" % len(collection["errors"])
            messagebox.showinfo("没有可导入的 PDF", message)
            self.status_var.set(message)
            return
        existing = {
            os.path.normcase(os.path.abspath(item.get("file_path") or ""))
            for item in self.items
        }
        added = 0
        duplicate_count = 0
        added_codes: set[str] = set()
        for path in pdf_paths:
            path = os.path.abspath(str(path))
            if os.path.normcase(path) in existing:
                duplicate_count += 1
                continue
            code, name, errors = drawing_bundle.parse_drawing_filename(path)
            kind, machine = drawing_bundle.infer_kind(code)
            parent_code = drawing_bundle.normalize_code(parent_code)
            relation_type = (
                drawing_bundle.default_link_type(parent_code, code)
                if parent_code and kind == "component"
                else ""
            )
            borrow = relation_type == "borrow"
            if kind == "component" and parent_code.startswith("03."):
                machine = parent_code if parent_code.startswith("03.") else ""
            elif parent_code and kind == "process_doc" and not machine:
                machine = parent_code if parent_code.startswith("03.") else ""
            item = {
                "source": os.path.basename(path),
                "page": 1,
                "file_path": path,
                "code": code,
                "name": "" if drawing_bundle.is_single_letter_process_code(code) else name,
                "machine": machine,
                "series_product_folder": (
                    drawing_bundle.series_product_folder(machine)
                    if kind == "machine_drawing"
                    else ""
                ),
                "kind": kind,
                "borrow": borrow,
                "borrow_to": parent_code if borrow else "",
                "parent_code": (
                    parent_code if relation_type == "own" else ""
                ),
                "upload_sel": not errors,
                "status": (
                    "文件名不符合"
                    if errors
                    else "待上传（次级条目）"
                    if borrow
                    else "待上传"
                ),
                "target": "",
                "note": "；".join(errors),
            }
            drawing_bundle.register_fingerprint(item, force=True)
            self.items.append(item)
            existing.add(os.path.normcase(path))
            added += 1
            if code:
                added_codes.add(code)
            if parent_code and kind == "component":
                self._add_relation(
                    parent_code,
                    code,
                    link_type=relation_type or None,
                    source="drag",
                    refresh=False,
                )
        if added:
            if self.bundle is None:
                self.bundle = {
                    "schema": "caxa-drawing-upload-bundle",
                    "schema_version": 1,
                    "items": self.items,
                    "assemblies": [],
                    "manual_borrows": self.manual_borrows,
                    "relations": self.relations,
                    "warnings": [],
                }
            else:
                self.bundle["items"] = self.items
                self.bundle["relations"] = self.relations
            self._ensure_batch_id()
            inferred = drawing_bundle.infer_code_relations(
                self.bundle,
                child_codes=added_codes,
                log=self.log,
            )
            self.relations = list(self.bundle["relations"])
            self.bundle["relations"] = self.relations
            self._sync_relation_item_fields()
            self.bundle_var.set("手工拖入的 PDF：%d 项" % len(self.items))
            self.refresh_tree()
            message = "已加入 %d 项" % added
            if inferred:
                message += "，按图号自动匹配 %d 条子级关系" % len(inferred)
            if duplicate_count:
                message += "，跳过已存在 %d 项" % duplicate_count
            if ignored_count:
                message += "，忽略 %d 个文件夹或非 PDF 文件" % ignored_count
            if collection["errors"]:
                message += "，%d 个路径读取失败" % len(collection["errors"])
            message += "；标红行可双击修正或点“规范并预检”"
            self.status_var.set(message)
            self.log(message)

    def normalize_and_precheck(self):
        if self._blocked_by_busy():
            return
        if not self.items:
            messagebox.showinfo("提示", "当前列表中没有图纸条目")
            return
        self._commit_inline_edit()
        result = self.normalize_filenames(show_feedback=False)
        message = "文件名规范完成：更新 %d 项" % result["updated"]
        if result["skipped"]:
            message += "，需人工修正 %d 项" % result["skipped"]
        message += "；继续执行本地检查与 CAXA 预检"
        self.status_var.set(message)
        self.log(message)
        self.check_bundle()

    def _confirmed_pdf_fingerprint(self, item: dict) -> dict | None:
        """Return the observed fingerprint, or None when a change is declined."""
        path = item.get("file_path") or ""
        if not os.path.isfile(path):
            return {}
        digest, size = drawing_bundle.compute_fingerprint(path)
        expected_hash = str(item.get("sha256") or "").lower()
        expected_size = item.get("size")
        changed = (
            (expected_hash and expected_hash != digest)
            or (expected_size is not None and str(expected_size) != str(size))
        )
        if changed and not messagebox.askyesno(
            "PDF 内容已变更",
            "文件与导入时的内容不同：\n%s\n\n"
            "确认使用当前 PDF 内容？" % path,
            parent=self,
        ):
            return None
        return {
            "sha256": digest,
            "size": size,
            "fingerprint_source": item.get("fingerprint_source") or "import",
        }

    def normalize_filenames(self, show_feedback=True):
        """Fill code/name/machine from the file name; never rename the source.

        Uploads always use the canonical target name through staging, so the
        user's files stay untouched.
        """
        if self._blocked_by_busy():
            return {"updated": 0, "skipped": 0, "errors": []}
        if not self.items:
            if show_feedback:
                messagebox.showinfo("提示", "当前列表中没有图纸条目")
            return {"updated": 0, "skipped": 0, "errors": []}
        updated_count = 0
        skipped_errors = []
        updates = []
        code_mapping = {}
        for item in self.items:
            file_path = item.get("file_path") or ""
            if not file_path or not os.path.isfile(file_path):
                continue
            fields, errors = ui_logic.normalized_filename_fields(item, file_path)
            if errors:
                skipped_errors.append(
                    "%s：%s" % (os.path.basename(file_path), "；".join(errors))
                )
                continue
            try:
                fingerprint = self._confirmed_pdf_fingerprint(item)
            except OSError as exc:
                skipped_errors.append("%s：读取 PDF 失败（%s）" % (os.path.basename(file_path), exc))
                continue
            if fingerprint is None:
                skipped_errors.append("%s：PDF 内容变更尚未确认" % os.path.basename(file_path))
                continue
            code = fields["code"]
            old_code = drawing_bundle.normalize_code(item.get("code"))
            if old_code and old_code != code:
                code_mapping[old_code] = code
            updates.append(
                (
                    item,
                    old_code,
                    str(item.get("kind") or ""),
                    drawing_bundle.normalize_code(item.get("machine")),
                    fields,
                    fingerprint,
                )
            )

        ui_logic.rekey_planned_codes(
            self.items,
            self.relations,
            self.manual_borrows,
            code_mapping,
        )
        for item, old_code, old_kind, old_machine, fields, fingerprint in updates:
            content_changed = bool(
                item.get("sha256")
                and fingerprint.get("sha256")
                and str(item["sha256"]).lower() != fingerprint["sha256"]
            )
            item.update(fields)
            if fields["kind"] == "machine_drawing":
                item["series_product_folder"] = (
                    str(item.get("series_product_folder") or "").strip()
                    or drawing_bundle.series_product_folder(fields["code"])
                )
            elif fields["kind"] == "component":
                item["series_product_name"] = ""
                item["series_product_folder"] = ""
            item.update(fingerprint)
            item["filename_error"] = ""
            item["note"] = ""
            if item.get("status") == "文件名不符合":
                item["upload_sel"] = True
                item["status"] = "待上传（次级条目）" if item.get("borrow") else "待上传"
            elif content_changed or (
                old_code,
                old_kind,
                old_machine,
            ) != (
                fields["code"],
                fields["kind"],
                fields["machine"],
            ):
                ui_logic.invalidate_item(item, "文件名已规范化，需重新上传")
            updated_count += 1

        self._series_product_required_codes.clear()
        if self.bundle is not None:
            self.bundle["items"] = self.items
            self.bundle["relations"] = self.relations
            self.bundle["manual_borrows"] = self.manual_borrows
        self.refresh_tree()
        msg = "已按文件名补全 %d 项；上传时按规范目标名提交，源文件不改名" % updated_count
        if skipped_errors:
            msg += "；另有 %d 项需人工双击修正：\n%s" % (
                len(skipped_errors),
                "\n".join(skipped_errors[:8]),
            )
            if show_feedback:
                messagebox.showwarning("文件名规范化结果", msg)
            else:
                self.status_var.set(msg)
                self.log(msg)
        else:
            self.status_var.set(msg)
            self.log(msg)
        return {
            "updated": updated_count,
            "skipped": len(skipped_errors),
            "errors": skipped_errors,
        }

    def open_bundle(self):
        path = filedialog.askopenfilename(
            title="选择 manifest.json 或上传包目录内的 manifest.json",
            filetypes=[("上传包清单", "manifest.json"), ("JSON", "*.json")],
        )
        if not path:
            return
        self._load_bundle_from_path(path)

    def open_bundle_dir(self):
        path = filedialog.askdirectory(title="选择图纸上传包目录")
        if not path:
            return
        self._load_bundle_from_path(path)

    def _load_bundle_from_path(self, path: str):
        if self._blocked_by_busy():
            return
        try:
            manifest_path, bundle = drawing_bundle.load_bundle(path)
            self._close_inline_editor()
            self._series_product_required_codes.clear()
            self.manifest_path = manifest_path
            self.bundle = bundle
            self._ensure_batch_id()
            self.items = list(bundle.get("items") or [])
            self.manual_borrows = list(bundle.get("manual_borrows") or [])
            self.relations = drawing_bundle.normalize_manifest_relations(bundle)
            self._sync_relation_item_fields()
            self.bundle_var.set(str(manifest_path.parent))
            self.refresh_tree()
            inferred = int(bundle.get("auto_prefix_relation_count") or 0)
            self.status_var.set(
                "已加载 %d 项%s，可点击“上传”列切换选择"
                % (
                    len(self.items),
                    "，按图号自动匹配 %d 条子级关系" % inferred
                    if inferred
                    else "",
                )
            )
            self.log("已打开上传包：%s" % manifest_path.parent)
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def refresh_tree(self):
        selected_rows = self.tree.selection()
        selected_products = {
            self._tree_product_nodes[row] for row in selected_rows
            if row in self._tree_product_nodes
        }
        selected_codes = {
            self._tree_codes.get(row) for row in selected_rows if self._tree_codes.get(row)
        }
        collapsed = {
            code
            for iid, code in self._tree_codes.items()
            if self.tree.parent(iid) and not self.tree.item(iid, "open")
        }
        self.tree.delete(*self.tree.get_children())
        self._tree_items = {}
        self._tree_relations = {}
        self._tree_codes = {}
        self._tree_product_groups = {}
        self._tree_product_nodes = {}

        batch: dict[str, dict] = {}
        for index, item in enumerate(self.items):
            problems = drawing_bundle.item_problems(item, check_content=False)
            warnings = drawing_bundle.filename_warnings(item)
            item["filename_valid"] = not problems
            item["filename_warnings"] = warnings
            if problems:
                item["filename_error"] = "；".join(problems)
                item["upload_sel"] = False
                if item.get("status") in ("", "待上传", "待上传（次级条目）"):
                    item["status"] = "文件名不符合"
            else:
                item["filename_error"] = "；".join(warnings)
                if item.get("status") == "文件名不符合":
                    item["status"] = "待上传（次级条目）" if item.get("borrow") else "待上传"
                    item["upload_sel"] = True
            item["_tree_problem"], item["_tree_warning"] = problems, warnings
            code = drawing_bundle.normalize_code(item.get("code"))
            if code:
                batch.setdefault(code, item)

        machine_targets = {
            target["machine"]: target
            for target in collect_machine_targets(self.items)
            if target.get("family") == "03"
        }

        grouped: dict[str, list[dict]] = {}
        for relation in self.relations:
            parent = drawing_bundle.normalize_code(relation.get("parent_code"))
            child = drawing_bundle.normalize_code(relation.get("child_code"))
            if parent and child:
                grouped.setdefault(parent, []).append(relation)

        child_codes = {
            drawing_bundle.normalize_code(rel.get("child_code"))
            for rel in self.relations
            if drawing_bundle.normalize_code(rel.get("child_code"))
        }
        root_codes = list(
            dict.fromkeys(
                drawing_bundle.normalize_code(item.get("code"))
                for item in self.items
                if drawing_bundle.normalize_code(item.get("code")) not in child_codes
            )
        )
        root_codes.extend(
            parent for parent in sorted(grouped) if parent not in batch and parent not in child_codes
        )

        row_number = 0
        rendered_codes = set()
        folder_groups: dict[str, str] = {}

        def insert_product_group(
            folder_path: str,
            parent_iid: str = "",
        ) -> str:
            nonlocal row_number
            row_number += 1
            iid = "g%d" % row_number
            folder_name = folder_path.rsplit("/", 1)[-1]
            label = "产品目录 %s" % folder_name
            self.tree.insert(
                parent_iid,
                "end",
                iid=iid,
                text=label,
                values=(
                    "CAXA 产品目录",
                    "",
                    "系列目录",
                    folder_path,
                    "产品父级",
                    "",
                    "—",
                    "",
                    "待关联",
                    "",
                ),
                tags=("product_group",),
                open=True,
            )
            self._tree_items[iid] = None
            self._tree_relations[iid] = None
            self._tree_codes[iid] = ""
            self._tree_product_groups[iid] = folder_path
            return iid

        def insert_product_node(folder_iid: str, code: str, item: dict) -> str:
            nonlocal row_number
            row_number += 1
            iid = "r%d" % row_number
            product_name = str(item.get("series_product_name") or "").strip()
            required = code in self._series_product_required_codes
            label = "系列产品 %s" % (product_name or "（未命名）")
            self.tree.insert(
                folder_iid,
                "end",
                iid=iid,
                text=label,
                values=(
                    "CAXA 产品",
                    "",
                    "系列产品",
                    code,
                    "产品父级",
                    "",
                    "—",
                    "",
                    "必填" if required and not product_name else (
                        "已命名" if product_name else "名称待填写"
                    ),
                    product_name,
                ),
                tags=("product_group",),
                open=True,
            )
            self._tree_items[iid] = None
            self._tree_relations[iid] = None
            self._tree_codes[iid] = ""
            self._tree_product_nodes[iid] = code
            return iid

        product_nodes: dict[str, str] = {}

        def ensure_product_node(code: str) -> str:
            code = drawing_bundle.normalize_code(code)
            if code in product_nodes:
                return product_nodes[code]
            target = machine_targets.get(code) or {}
            item = self._series_product_plan_item(code) or {}
            folder_name = (
                str(target.get("series_product_folder") or "").strip()
                or str(item.get("series_product_folder") or "").strip()
                or drawing_bundle.series_product_folder(code)
                or "未指定系列"
            )
            item["series_product_folder"] = folder_name
            parent_group = ""
            parts = [
                part.strip()
                for part in folder_name.replace("\\", "/").split("/")
                if part.strip()
            ]
            for index in range(len(parts)):
                folder_path = "/".join(parts[: index + 1])
                group_iid = folder_groups.get(folder_path)
                if group_iid is None:
                    group_iid = insert_product_group(
                        folder_path,
                        parent_group,
                    )
                    folder_groups[folder_path] = group_iid
                parent_group = group_iid
            product_iid = insert_product_node(
                parent_group, code, item
            )
            product_nodes[code] = product_iid
            return product_iid

        def insert_node(
            code: str,
            parent_iid: str = "",
            relation: dict | None = None,
            ancestors: tuple[str, ...] = (),
        ):
            nonlocal row_number
            if not code or code in ancestors:
                return
            item = batch.get(code)
            row_number += 1
            iid = "n%d" % row_number
            name = str(item.get("name") or "") if item else ""
            label = " ".join(value for value in (code, name) if value)
            if item:
                problems = item.pop("_tree_problem", [])
                warnings = item.pop("_tree_warning", [])
                relation_label = ""
                if relation is not None:
                    link_type = str(relation.get("link_type") or "borrow")
                    relation_label = LINK_TYPE_LABEL.get(link_type, link_type)
                    if relation.get("existing_original"):
                        relation_label += " [已有原件→借用]"
                    if relation.get("manual_override"):
                        relation_label += " [人工]"
                kind_text = KIND_LABEL.get(
                    item.get("kind"), item.get("kind", "")
                )
                if item.get("existing_original"):
                    kind_text += "（已有原件）"
                upgrade_only_text = ""
                if (
                    item.get("existing_original")
                    and str(item.get("kind") or "") == "component"
                ):
                    upgrade_only_text = (
                        "☑ 不借用"
                        if item.get("upgrade_only")
                        else "☐ 按计划借用"
                    )
                values = (
                    item.get("source", ""),
                    item.get("page", ""),
                    kind_text,
                    item.get("machine", ""),
                    relation_label or ("父级" if grouped.get(code) else ""),
                    "异常" if problems else ("需规范" if warnings else "合规"),
                    "是" if item.get("upload_sel", True) else "否",
                    upgrade_only_text,
                    item.get("status", ""),
                    item.get("note", "") or item.get("filename_error", ""),
                )
                tags = ("invalid",) if problems else ()
            else:
                link_type = str((relation or {}).get("link_type") or "borrow")
                relation_label = LINK_TYPE_LABEL.get(link_type, link_type)
                if (relation or {}).get("manual_override"):
                    relation_label += " [人工]"
                if (relation or {}).get("existing_original"):
                    relation_label += " [已有原件→借用]"
                values = (
                    "设计树节点",
                    "",
                    "外部节点",
                    code if code.startswith("03.") else "",
                    relation_label,
                    "",
                    "—",
                    "",
                    (relation or {}).get("link_status", "") or "待处理",
                    (relation or {}).get("note", ""),
                )
                tags = ("external",)
            self.tree.insert(
                parent_iid,
                "end",
                iid=iid,
                text=label,
                values=values,
                tags=tags,
                open=code not in collapsed,
            )
            self._tree_items[iid] = item
            self._tree_relations[iid] = relation
            self._tree_codes[iid] = code
            rendered_codes.add(code)
            for child_relation in grouped.get(code, []):
                child = drawing_bundle.normalize_code(
                    child_relation.get("child_code")
                )
                insert_node(
                    child,
                    iid,
                    child_relation,
                    ancestors + (code,),
                )

        def insert_root(code: str) -> None:
            if not code or code in rendered_codes:
                return
            item = batch.get(code)
            if item and str(item.get("kind") or "") == "machine_drawing":
                product_iid = ensure_product_node(code)
                insert_node(code, product_iid)
            elif item and str(item.get("kind") or "") == "process_doc":
                machine = drawing_bundle.normalize_code(item.get("machine"))
                if machine in machine_targets:
                    insert_node(code, ensure_product_node(machine))
                else:
                    insert_node(code)
            else:
                insert_node(code)

        for code in root_codes:
            insert_root(code)
        for item in self.items:
            code = drawing_bundle.normalize_code(item.get("code"))
            if code and code not in rendered_codes:
                insert_root(code)

        restored = [iid for iid, code in self._tree_codes.items() if code in selected_codes]
        restored.extend(
            iid for iid, code in self._tree_product_nodes.items() if code in selected_products
        )
        if restored:
            self.tree.selection_set(*restored)
            self.tree.see(restored[0])

    def _batch_codes(self) -> set[str]:
        return {
            drawing_bundle.normalize_code(it.get("code"))
            for it in self.items
            if drawing_bundle.is_valid_code(it.get("code"))
        }

    def _add_relation(
        self,
        parent_code: str,
        child_code: str,
        *,
        link_type: str | None = None,
        source: str = "manual",
        manual_override: bool = False,
        link_only: bool = False,
        refresh: bool = True,
    ) -> bool:
        parent = drawing_bundle.normalize_code(parent_code)
        child = drawing_bundle.normalize_code(child_code)
        if (
            not drawing_bundle.is_valid_code(parent)
            or not drawing_bundle.is_valid_code(child)
            or parent == child
        ):
            return False
        resolved_type = (
            link_type
            if link_type in ("own", "borrow")
            else drawing_bundle.default_link_type(parent, child, self._batch_codes())
        )
        for existing in self.relations:
            if (
                drawing_bundle.normalize_code(existing.get("parent_code")) == parent
                and drawing_bundle.normalize_code(existing.get("child_code")) == child
            ):
                if manual_override or link_type in ("own", "borrow"):
                    existing["link_type"] = resolved_type
                    existing["manual_override"] = bool(manual_override)
                if link_only:
                    existing["link_only"] = True
                self._sync_relation_item_fields()
                if refresh:
                    self.refresh_relations()
                return False

        # A drawing has at most one ownership parent.  Moving its ownership
        # replaces that edge, while borrow references to machines or other
        # assemblies remain valid and may exist in parallel.
        if resolved_type == "own":
            self.relations = [
                existing
                for existing in self.relations
                if not (
                    drawing_bundle.normalize_code(existing.get("child_code")) == child
                    and str(existing.get("link_type") or "") == "own"
                )
            ]
        relation = {
            "parent_code": parent,
            "child_code": child,
            "link_type": resolved_type,
            "source": source,
            "manual_override": bool(manual_override),
            "link_only": bool(link_only),
            "link_status": "",
        }
        self.relations.append(relation)
        if self.bundle is None:
            self.bundle = {
                "schema": "caxa-drawing-upload-bundle",
                "schema_version": 1,
                "items": self.items,
                "assemblies": [],
                "manual_borrows": self.manual_borrows,
                "relations": self.relations,
                "warnings": [],
            }
        self._ensure_batch_id()
        self.bundle["relations"] = self.relations
        self._sync_relation_item_fields()
        if refresh:
            self.refresh_relations()
        return True

    def _sync_relation_item_fields(self):
        """Project single-parent relations onto legacy item convenience fields."""
        own_parent_by_child = {}
        borrow_parent_by_child = {}
        for relation in self.relations:
            child = drawing_bundle.normalize_code(relation.get("child_code"))
            parent = drawing_bundle.normalize_code(relation.get("parent_code"))
            if child and parent:
                if str(relation.get("link_type") or "") == "own":
                    own_parent_by_child[child] = parent
                elif str(relation.get("link_type") or "") == "borrow":
                    borrow_parent_by_child.setdefault(child, parent)
        for item in self.items:
            code = drawing_bundle.normalize_code(item.get("code"))
            own_parent = own_parent_by_child.get(code, "")
            borrow_parent = borrow_parent_by_child.get(code, "")
            item["borrow"] = bool(borrow_parent)
            item["borrow_to"] = borrow_parent
            item["parent_code"] = own_parent
            status = str(item.get("status") or "")
            if item["borrow"] and status in ("", "待上传"):
                item["status"] = "待上传（次级条目）"
            elif not item["borrow"] and status == "待上传（次级条目）":
                item["status"] = "待上传"

    def _code_name_map(self):
        result = {}
        for item in self.items:
            code = drawing_bundle.normalize_code(item.get("code"))
            if code:
                result[code] = str(item.get("name") or "")
        return result

    def refresh_relations(self):
        self.refresh_tree()

    def toggle_selected_relation_type(self):
        if self._blocked_by_busy():
            return
        selection = self.tree.selection()
        if not selection:
            return
        rel = self._tree_relations.get(selection[0])
        if not rel:
            return
        current = str(rel.get("link_type") or "borrow")
        new_type = "borrow" if current == "own" else "own"
        child = drawing_bundle.normalize_code(rel.get("child_code"))
        if new_type == "own":
            self.relations = [
                relation
                for relation in self.relations
                if relation is rel
                or not (
                    drawing_bundle.normalize_code(relation.get("child_code"))
                    == child
                    and str(relation.get("link_type") or "") == "own"
                )
            ]
        rel["link_type"] = new_type
        rel["manual_override"] = True
        self._sync_relation_item_fields()
        if self.bundle is not None:
            self.bundle["relations"] = self.relations
        self.refresh_tree()
        self.log(
            "关系类型已手动切换：%s ← %s 设为【%s】"
            % (
                rel.get("parent_code"),
                rel.get("child_code"),
                LINK_TYPE_LABEL.get(new_type, new_type),
            )
        )

    def _begin_row_drag(self, event):
        row = self.tree.identify_row(event.y)
        if (
            self.busy
            or not row
            or self._drawing_column_at(event.x) in (UPLOAD_COLUMN, UPGRADE_ONLY_COLUMN)
        ):
            self._drag_source_row = ""
            self._drag_source_rows = ()
            return
        current_selection = tuple(self.tree.selection())
        item_rows = tuple(
            selected
            for selected in current_selection
            if self._tree_items.get(selected) is not None
        )
        if row in item_rows:
            self._drag_source_rows = item_rows
        else:
            self._drag_source_rows = (
                (row,) if self._tree_items.get(row) is not None else ()
            )
        self._drag_source_row = row
        self._drag_started = False
        self._drag_origin = (event.x_root, event.y_root)
        # Keep a prepared multi-selection intact when dragging one of its rows.
        # Ctrl/Shift clicks retain the normal Treeview selection behaviour.
        if (
            len(self._drag_source_rows) > 1
            and row in self._drag_source_rows
            and not (int(getattr(event, "state", 0)) & 0x0005)
        ):
            return "break"

    def _begin_row_drag_motion(self, event):
        if not self._drag_source_rows or self.busy:
            return
        if not self._drag_started and self._drag_origin:
            dx = abs(int(event.x_root) - int(self._drag_origin[0]))
            dy = abs(int(event.y_root) - int(self._drag_origin[1]))
            if max(dx, dy) < 6:
                return
            self._drag_started = True
            self._show_drag_ghost(event)
            try:
                self.tree.grab_set()
            except tk.TclError:
                pass
        self._move_drag_ghost(event)
        row = self.tree.identify_row(event.y)
        target_code = self._tree_codes.get(row, "")
        source_codes = {
            self._tree_codes.get(source, "")
            for source in self._drag_source_rows
            if self._tree_codes.get(source, "")
        }
        if (
            row
            and target_code
            and target_code not in source_codes
            and not any(
                self._is_descendant_code(target_code, source)
                for source in source_codes
            )
        ):
            self._set_drag_target(row)
        else:
            self._set_drag_target("")

    def _finish_row_drag(self, event):
        sources = tuple(self._drag_source_rows)
        if not sources and self._drag_source_row:
            sources = (self._drag_source_row,)
        started = self._drag_started
        target = self._drag_target_row or self.tree.identify_row(event.y)
        self._clear_drag_visuals()
        self._drag_source_row = ""
        self._drag_source_rows = ()
        self._drag_started = False
        self._drag_origin = None
        if self.busy or not started or not sources or not target:
            return
        parent_code = self._tree_codes.get(target, "")
        if not parent_code:
            return
        changed_codes = []
        unchanged_codes = []
        for source in sources:
            if source == target or self._tree_items.get(source) is None:
                continue
            child_code = self._tree_codes.get(source, "")
            if (
                not child_code
                or child_code == parent_code
                or self._is_descendant_code(parent_code, child_code)
            ):
                continue
            if self._add_relation(
                parent_code, child_code, source="drag", refresh=False
            ):
                changed_codes.append(child_code)
            else:
                unchanged_codes.append(child_code)
        if changed_codes or unchanged_codes:
            self._sync_relation_item_fields()
            if self.bundle is not None:
                self.bundle["relations"] = self.relations
            self.refresh_tree()
            if len(changed_codes) + len(unchanged_codes) == 1:
                child_code = (changed_codes or unchanged_codes)[0]
                self.status_var.set(
                    "%s：%s ← %s"
                    % (
                        "已建立次级条目" if changed_codes else "关系已存在",
                        parent_code,
                        child_code,
                    )
                )
            else:
                self.status_var.set(
                    "已处理 %d 项到父级 %s：新建/移动 %d 项，已存在 %d 项"
                    % (
                        len(changed_codes) + len(unchanged_codes),
                        parent_code,
                        len(changed_codes),
                        len(unchanged_codes),
                    )
                )
            self.log(self.status_var.get())

    def _show_drag_ghost(self, event):
        if self._drag_ghost is not None:
            return
        source_rows = self._drag_source_rows or (self._drag_source_row,)
        item = self._tree_items.get(source_rows[0]) or {}
        code = drawing_bundle.normalize_code(item.get("code"))
        name = str(item.get("name") or "")
        label = " ".join(value for value in (code, name) if value)
        if len(source_rows) > 1:
            text = "PDF × %d  %s 等" % (len(source_rows), label)
        else:
            text = "PDF  " + label
        self._drag_ghost = tk.Label(
            self.tree,
            text=text,
            bg="#eef7ff",
            fg="#17324d",
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=5,
            font=("Segoe UI", 9, "bold"),
        )
        self._drag_ghost.lift()
        self._move_drag_ghost(event)

    def _move_drag_ghost(self, event):
        if self._drag_ghost is None:
            return
        x = int(event.x_root) - self.tree.winfo_rootx() + 14
        y = int(event.y_root) - self.tree.winfo_rooty() + 12
        x = max(0, min(x, max(0, self.tree.winfo_width() - 260)))
        y = max(0, min(y, max(0, self.tree.winfo_height() - 32)))
        self._drag_ghost.place(x=x, y=y, anchor="nw")
        self._drag_ghost.lift()

    def _is_descendant_code(self, code: str, ancestor: str) -> bool:
        current = drawing_bundle.normalize_code(code)
        target = drawing_bundle.normalize_code(ancestor)
        if not current or not target:
            return False
        parent_by_child = {
            drawing_bundle.normalize_code(relation.get("child_code")): relation
            for relation in self.relations
            if drawing_bundle.normalize_code(relation.get("child_code"))
        }
        seen = set()
        while current and current not in seen:
            seen.add(current)
            if current == target:
                return True
            relation = parent_by_child.get(current)
            current = (
                drawing_bundle.normalize_code(relation.get("parent_code"))
                if relation
                else ""
            )
        return False

    def _set_drag_target(self, row: str):
        if row == self._drag_target_row:
            return
        if self._drag_target_row:
            try:
                tags = tuple(
                    tag
                    for tag in self.tree.item(self._drag_target_row, "tags")
                    if tag != "drop_target"
                )
                self.tree.item(self._drag_target_row, tags=tags)
            except tk.TclError:
                pass
        self._drag_target_row = row
        if row:
            try:
                tags = tuple(self.tree.item(row, "tags"))
                self.tree.item(row, tags=tags + ("drop_target",))
            except tk.TclError:
                self._drag_target_row = ""

    def _clear_drag_visuals(self):
        self._set_drag_target("")
        if self._drag_ghost is not None:
            try:
                self._drag_ghost.destroy()
            except tk.TclError:
                pass
            self._drag_ghost = None
        try:
            self.tree.grab_release()
        except tk.TclError:
            pass

    def _extract_code_text(self, text):
        value = str(text or "").strip()
        if not value:
            return ""
        return drawing_bundle.normalize_code(value.split()[0])

    def add_relation_dialog(self, parent_code=""):
        if self._blocked_by_busy():
            return
        selection = self.tree.selection()
        if not parent_code and selection:
            parent_code = self._tree_codes.get(selection[0], "")
        dialog = tk.Toplevel(self)
        dialog.title("借用已有零件")
        dialog.transient(self)
        dialog.grab_set()
        form = ttk.Frame(dialog, padding=12)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text="上级图号").grid(
            row=0, column=0, sticky="e", pady=4
        )
        parent_var = tk.StringVar(value=parent_code)
        ttk.Entry(form, textvariable=parent_var, width=52).grid(
            row=0, column=1, sticky="w", pady=4
        )
        ttk.Label(form, text="借用零件图号").grid(
            row=1, column=0, sticky="e", pady=4
        )
        child_var = tk.StringVar()
        child_entry = ttk.Entry(form, textvariable=child_var, width=52)
        child_entry.grid(row=1, column=1, sticky="w", pady=4)

        ttk.Label(
            form,
            text="请填写 CAXA 设计树里已有的零件图号。",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 8))

        def save():
            parent = drawing_bundle.normalize_code(parent_var.get())
            child = self._extract_code_text(child_var.get())
            if not drawing_bundle.is_valid_code(parent):
                messagebox.showwarning("上级图号无效", "请填写有效的上级图号。")
                return
            if not drawing_bundle.is_valid_code(child):
                messagebox.showwarning(
                    "借用零件图号无效", "请输入 CAXA 设计树中已有的零件图号。"
                )
                return
            if not self._add_relation(
                parent,
                child,
                link_type="borrow",
                source="manual",
                manual_override=True,
                link_only=True,
            ):
                messagebox.showinfo("提示", "该借用关系已在上传计划中。")
            self.refresh_tree()
            self.log("已添加借用计划：%s ← %s" % (parent, child))
            dialog.destroy()

        ttk.Button(form, text="加入借用计划", command=save).grid(
            row=3, column=0, columnspan=2, pady=(6, 0)
        )
        self._center_dialog_on_parent(dialog)
        dialog.after(50, child_entry.focus_set)

    def delete_selected_relation(self):
        if self._blocked_by_busy():
            return
        selection = self.tree.selection()
        if not selection:
            return
        row = selection[0]
        rel = self._tree_relations.get(row)
        if rel:
            self._remove_relations([rel])
            if self.bundle is not None:
                self.bundle["relations"] = self.relations
            self.refresh_tree()
            return
        parent = self._tree_codes.get(row)
        if parent:
            removed = [
                rel for rel in self.relations
                if drawing_bundle.normalize_code(rel.get("parent_code")) == parent
            ]
            self._remove_relations(removed)
            if self.bundle is not None:
                self.bundle["relations"] = self.relations
            self.refresh_tree()
            self.log("已删除父级 %s 下的 %d 条关系" % (parent, len(removed)))

    def _remove_relations(self, relations: list[dict]):
        """Canonical removal: relations list and item convenience fields."""
        pairs = {
            (
                drawing_bundle.normalize_code(rel.get("parent_code")),
                drawing_bundle.normalize_code(rel.get("child_code")),
            )
            for rel in relations
        }
        self.relations = [
            rel for rel in self.relations
            if (
                drawing_bundle.normalize_code(rel.get("parent_code")),
                drawing_bundle.normalize_code(rel.get("child_code")),
            )
            not in pairs
        ]
        remaining_own: dict[str, str] = {}
        remaining_borrow: dict[str, str] = {}
        for rel in self.relations:
            child = drawing_bundle.normalize_code(rel.get("child_code"))
            parent = drawing_bundle.normalize_code(rel.get("parent_code"))
            if child and parent:
                if str(rel.get("link_type") or "") == "own":
                    remaining_own[child] = parent
                elif str(rel.get("link_type") or "") == "borrow":
                    remaining_borrow.setdefault(child, parent)
        removed_children = {child for _, child in pairs}
        for item in self.items:
            code = drawing_bundle.normalize_code(item.get("code"))
            if code in removed_children:
                own_parent = remaining_own.get(code, "")
                borrow_parent = remaining_borrow.get(code, "")
                item["borrow"] = bool(borrow_parent)
                item["borrow_to"] = borrow_parent
                item["parent_code"] = own_parent

    def _show_row_menu(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        selected = set(self.tree.selection())
        if row not in selected:
            self.tree.selection_set(row)
        item = self._tree_items.get(row)
        product_code = self._tree_product_nodes.get(row, "")
        series_path = self._tree_product_groups.get(row, "")
        relation = self._tree_relations.get(row)
        code = self._tree_codes.get(row, "")
        menu = tk.Menu(self, tearoff=False)
        if series_path:
            menu.add_command(
                label="修改上传目标系列…",
                command=lambda: self._begin_inline_edit(row, mode="series"),
            )
        elif product_code:
            menu.add_command(
                label="修改/新建计划产品名称…",
                command=lambda: self._begin_inline_edit(row, mode="product"),
            )
            menu.add_command(
                label="修改完整信息…",
                command=self._edit_item,
            )
        elif item is not None:
            menu.add_command(
                label="原地重命名 / 修改文件名…",
                command=lambda: self._begin_inline_edit(row, mode="filename"),
            )
            menu.add_command(
                label="修改完整信息…",
                command=self._edit_item,
            )
        if code:
            menu.add_command(
                label="借用已有零件…",
                command=lambda: self.add_relation_dialog(code),
            )
        if item is not None:
            menu.add_command(
                label="规范文件名",
                command=self.normalize_filenames,
            )
            menu.add_separator()
            menu.add_command(
                label="从列表中删除所选图纸",
                command=self.delete_selected_drawings,
            )
        if relation is not None:
            menu.add_separator()
            menu.add_command(
                label="切换归属 / 借用",
                command=self.toggle_selected_relation_type,
            )
            menu.add_command(
                label="从计划中移除此关系",
                command=self.delete_selected_relation,
            )
        elif code and any(relation.get("parent_code") == code for relation in self.relations):
            menu.add_separator()
            menu.add_command(
                label="从计划中移除此上级的所有关系",
                command=self.delete_selected_relation,
            )
        menu.tk_popup(event.x_root, event.y_root)

    def delete_selected_drawings(self):
        """Remove selected drawing rows from the upload plan only."""
        if self._blocked_by_busy():
            return
        selected_items = [
            item
            for row in self.tree.selection()
            if (item := self._tree_items.get(row)) is not None
        ]
        if not selected_items:
            messagebox.showinfo("从列表删除图纸", "请先选中要移除的图纸行。")
            return
        removed_codes = {
            drawing_bundle.normalize_code(item.get("code"))
            for item in selected_items
            if drawing_bundle.normalize_code(item.get("code"))
        }
        answer = messagebox.askyesno(
            "从列表中删除所选图纸",
            "将从当前上传列表移除 %d 项图纸，并清理相关次级/借用关系。\n"
            "仅从列表移除，不改本地 PDF，也不删除 CAXA 远端对象。\n\n"
            "是否继续？" % len(removed_codes),
        )
        if not answer:
            return
        cleaned = ui_logic.remove_planned_drawings(
            self.items,
            self.relations,
            self.manual_borrows,
            removed_codes,
        )
        self.items = cleaned["items"]
        self.relations = cleaned["relations"]
        self.manual_borrows = cleaned["manual_borrows"]
        remaining_products = ui_logic.machine_product_codes(self.items)
        self._series_product_required_codes.intersection_update(
            remaining_products
        )
        if self.bundle is not None:
            self.bundle["items"] = self.items
            self.bundle["relations"] = self.relations
            self.bundle["manual_borrows"] = self.manual_borrows
        self.refresh_tree()
        removed_count = len(removed_codes)
        self.status_var.set("已从列表移除 %d 项图纸" % removed_count)
        self.log(
            "已从上传列表移除 %d 项图纸及关联关系；未修改本地文件或 CAXA"
            % removed_count
        )

    def _show_relation_menu(self, event):
        self._show_row_menu(event)

    def _toggle_upload(self, event):
        if self._blocked_by_busy():
            return
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        column = self._drawing_column_at(event.x)
        if column not in (UPLOAD_COLUMN, UPGRADE_ONLY_COLUMN):
            return
        row = self.tree.identify_row(event.y)
        if not row:
            return
        item = self._tree_items.get(row)
        if item is None:
            return
        if column == UPGRADE_ONLY_COLUMN:
            if not item.get("existing_original"):
                return
            item["upgrade_only"] = not bool(item.get("upgrade_only"))
            item["upgrade_only_user_set"] = True
            item["upgrade_only_defaulted"] = False
            if item["upgrade_only"]:
                self.status_var.set(
                    "%s：处理图纸，跳过归属和借用关系"
                    % drawing_bundle.normalize_code(item.get("code"))
                )
            else:
                self.status_var.set(
                    "%s：处理图纸后，按上传计划借用"
                    % drawing_bundle.normalize_code(item.get("code"))
                )
            self.refresh_tree()
            return
        if not item.get("filename_valid", True):
            messagebox.showwarning(
                "文件名不符合",
                "请先双击修改或点击“规范并预检”修正后再勾选。",
            )
            return
        item["upload_sel"] = not bool(item.get("upload_sel", True))
        self.refresh_tree()

    def _item_by_code(self, code: str):
        code = drawing_bundle.normalize_code(code)
        return next(
            (
                item for item in self.items
                if drawing_bundle.normalize_code(item.get("code")) == code
            ),
            None,
        )

    def _series_product_items(self, code: str) -> list[dict]:
        code = drawing_bundle.normalize_code(code)
        if not code.startswith("03."):
            return []
        return [
            item for item in self.items
            if (
                (
                    str(item.get("kind") or "") == "machine_drawing"
                    and drawing_bundle.normalize_code(item.get("code")) == code
                )
                or (
                    str(item.get("kind") or "") == "process_doc"
                    and drawing_bundle.normalize_code(item.get("machine")) == code
                )
            )
        ]

    def _series_product_plan_item(self, code: str) -> dict | None:
        items = self._series_product_items(code)
        drawing = next(
            (
                item for item in items
                if str(item.get("kind") or "") == "machine_drawing"
            ),
            None,
        )
        return drawing or (items[0] if items else None)

    def _set_series_product_name(self, code: str, name: str):
        code = drawing_bundle.normalize_code(code)
        name = str(name or "").strip()
        for item in self._series_product_items(code):
            item["series_product_name"] = name

    def _select_machine_code(self, code: str):
        code = drawing_bundle.normalize_code(code)
        iid = next(
            (
                row for row, value in self._tree_product_nodes.items()
                if value == code
            ),
            "",
        )
        if not iid:
            iid = next(
                (
                    row for row, value in self._tree_codes.items()
                    if value == code
                ),
                "",
            )
        if not iid:
            iid = next(
                (
                    row for row, item in self._tree_items.items()
                    if item is not None
                    and str(item.get("kind") or "") == "process_doc"
                    and drawing_bundle.normalize_code(item.get("machine")) == code
                ),
                "",
            )
        if iid:
            self.tree.selection_set(iid)
            self.tree.see(iid)
            self.tree.focus(iid)

    def _set_series_product_required(self, codes: set[str]):
        self._series_product_required_codes = {
            drawing_bundle.normalize_code(code) for code in codes if code
        }
        self.refresh_tree()

    def _on_tree_double_click(self, event):
        """Edit only when double-clicking the row body, never its tree indicator."""
        try:
            if self.tree.identify("region", event.x, event.y) != "tree":
                return
            element = str(self.tree.identify_element(event.x, event.y) or "").lower()
            if "indicator" in element:
                return
        except tk.TclError:
            return
        row = self.tree.identify_row(event.y)
        if row in self._tree_product_nodes:
            self._begin_inline_edit(row, mode="product")
            return "break"
        self._edit_item(event)

    def _close_inline_editor(self):
        editor = self._inline_editor
        self._inline_editor = None
        self._inline_target = ""
        self._inline_mode = ""
        if editor is not None:
            try:
                editor.destroy()
            except tk.TclError:
                pass

    def _begin_inline_edit(self, row: str, mode: str = "filename"):
        if self._blocked_by_busy():
            return
        item = self._tree_items.get(row)
        product_code = self._tree_product_nodes.get(row, "")
        series_path = self._tree_product_groups.get(row, "")
        if mode == "product":
            item = self._series_product_plan_item(product_code)
        elif mode == "filename" and item is None:
            item = self._series_product_plan_item(product_code)
        if mode not in ("series", "product", "filename"):
            return
        if mode == "series":
            if not series_path:
                return
            initial = series_path
        else:
            if item is None:
                return
            initial = (
                str(item.get("series_product_name") or "").strip()
                if mode == "product"
                else " ".join(
                    value
                    for value in (
                        drawing_bundle.normalize_code(item.get("code")),
                        str(item.get("name") or "").strip(),
                    )
                    if value
                )
            )
        if item is None and mode != "series":
            return
        self._close_inline_editor()
        self.tree.see(row)
        self.tree.update_idletasks()
        bbox = self.tree.bbox(row, "#0")
        if not bbox:
            return
        x, y, width, height = bbox
        editor = tk.Entry(
            self.tree,
            borderwidth=1,
            relief="solid",
            font=("Segoe UI", 9),
        )
        editor.insert(0, initial)
        editor.place(x=x, y=y, width=max(width, 260), height=height)
        editor.selection_range(0, "end")
        editor.bind("<Return>", self._commit_inline_edit)
        editor.bind("<Escape>", self._cancel_inline_edit)
        editor.bind("<FocusOut>", self._commit_inline_edit)
        self._inline_editor = editor
        self._inline_target = product_code if mode == "product" else row
        self._inline_mode = mode
        editor.focus_set()

    def _cancel_inline_edit(self, _event=None):
        self._close_inline_editor()
        return "break"

    def _commit_inline_edit(self, _event=None):
        editor = self._inline_editor
        if editor is None:
            return "break"
        value = editor.get()
        target = self._inline_target
        mode = self._inline_mode
        self._close_inline_editor()
        if mode == "filename":
            self._apply_inline_filename(target, value)
        elif mode == "product":
            self._apply_inline_product(target, value)
        elif mode == "series":
            self._apply_inline_series(target, value)
        return "break"

    def _apply_inline_filename(self, row: str, value: str):
        item = self._tree_items.get(row)
        if item is None and row in self._tree_product_nodes:
            item = self._item_by_code(self._tree_product_nodes[row])
        if item is None:
            return
        text = str(value or "").strip()
        if text.lower().endswith(".pdf"):
            text = text[:-4].rstrip()
        code, name, parse_errors = drawing_bundle.parse_drawing_filename(
            (text or "未命名") + ".pdf"
        )
        if parse_errors:
            messagebox.showwarning(
                "文件名不符合",
                "；".join(parse_errors),
            )
            return
        old_code = drawing_bundle.normalize_code(item.get("code"))
        old_name = str(item.get("name") or "").strip()
        if code == old_code and name == old_name:
            return
        new_kind, inferred_machine = drawing_bundle.infer_kind(code)
        new_machine = (
            inferred_machine
            if inferred_machine
            else (
                drawing_bundle.normalize_code(item.get("machine"))
                if new_kind == "component"
                else ""
            )
        )
        candidate = dict(item)
        candidate.update(
            code=code,
            name=name,
            kind=new_kind,
            machine=new_machine,
        )
        if new_kind == "machine_drawing":
            candidate["series_product_folder"] = (
                str(item.get("series_product_folder") or "").strip()
                or drawing_bundle.series_product_folder(code)
            )
        problems = drawing_bundle.item_problems(candidate, check_content=False)
        if problems:
            messagebox.showwarning("信息不完整", "；".join(problems))
            return
        try:
            fingerprint = self._confirmed_pdf_fingerprint(item)
        except OSError as exc:
            messagebox.showwarning("读取 PDF 失败", str(exc), parent=self)
            return
        if fingerprint is None:
            return
        if old_code and old_code != code:
            for relation in self.relations:
                if drawing_bundle.normalize_code(
                    relation.get("child_code")
                ) == old_code:
                    relation["child_code"] = code
                if drawing_bundle.normalize_code(
                    relation.get("parent_code")
                ) == old_code:
                    relation["parent_code"] = code
            for other in self.items:
                if other is item:
                    continue
                if drawing_bundle.normalize_code(
                    other.get("parent_code")
                ) == old_code:
                    other["parent_code"] = code
                if drawing_bundle.normalize_code(
                    other.get("borrow_to")
                ) == old_code:
                    other["borrow_to"] = code
        item.update(candidate)
        item.update(fingerprint)
        if self.bundle is not None:
            self.bundle["items"] = self.items
            self.bundle["relations"] = self.relations
        ui_logic.invalidate_item(item, "文件名已原地修改，需重新上传")
        self.refresh_tree()
        self.status_var.set("已原地修改文件名：%s" % text)
        self.log(self.status_var.get())

    def _apply_inline_product(self, code: str, value: str):
        members = self._series_product_items(code)
        if not members:
            return
        new_name = value.strip()
        if all(str(item.get("series_product_name") or "").strip() == new_name for item in members):
            return
        self._set_series_product_name(code, new_name)
        if self.bundle is not None:
            self.bundle["items"] = self.items
        self.refresh_tree()
        self.status_var.set(
            "已保存到当前上传计划：%s · %s" % (code, new_name or "已清空产品名称")
        )
        self.log(self.status_var.get())

    def _apply_inline_series(self, row: str, value: str):
        old_path = str(self._tree_product_groups.get(row) or "").strip("/")
        target_path = str(value or "").strip().strip("/")
        if not old_path:
            return
        target_path = target_path.replace("\\", "/")
        if not target_path:
            messagebox.showwarning(
                "目标系列无效",
                "请填写上传时要挂载的系列目录路径。",
            )
            return
        if target_path == old_path:
            return
        for item in self.items:
            folder = str(item.get("series_product_folder") or "").strip("/")
            if folder == old_path:
                item["series_product_folder"] = target_path
            elif folder.startswith(old_path + "/"):
                item["series_product_folder"] = (
                    target_path + folder[len(old_path):]
                )
        if self.bundle is not None:
            self.bundle["items"] = self.items
            self.bundle["relations"] = self.relations
        self.refresh_tree()
        action = (
            "已修改上传计划系列；上传时目录必须已存在，"
            "Product 可在既有目录下复用或新建"
        )
        self.status_var.set(action)
        self.log("%s：%s → %s" % (action, old_path, target_path))

    def _center_dialog_on_parent(self, dialog):
        dialog.update_idletasks()
        width = max(dialog.winfo_reqwidth(), dialog.winfo_width())
        height = max(dialog.winfo_reqheight(), dialog.winfo_height())
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 2)
        dialog.geometry("%dx%d+%d+%d" % (width, height, x, y))

    def _edit_item(self, _event=None):
        if self._blocked_by_busy():
            return
        selection = self.tree.selection()
        if not selection:
            return
        row = selection[0]
        item = self._tree_items.get(row)
        if item is None and row in self._tree_product_nodes:
            product_code = self._tree_product_nodes[row]
            item = next(
                (
                    candidate for candidate in self.items
                    if drawing_bundle.normalize_code(candidate.get("code"))
                    == product_code
                ),
                None,
            )
        if item is None:
            if self._tree_relations.get(row) is not None:
                self.toggle_selected_relation_type()
            return
        old_code = drawing_bundle.normalize_code(item.get("code"))
        old_parent = drawing_bundle.normalize_code(
            item.get("parent_code") or item.get("borrow_to")
        )
        dialog = tk.Toplevel(self)
        dialog.title("修改上传信息")
        dialog.transient(self)
        dialog.grab_set()
        form = ttk.Frame(dialog, padding=12)
        form.pack()
        values = {}
        for row, (key, label) in enumerate(
            (("code", "图号"), ("name", "图纸名称"),
             ("machine", drawing_bundle.MACHINE_FIELD_LABEL))
        ):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="e", pady=3)
            value = tk.StringVar(value=str(item.get(key) or ""))
            ttk.Entry(form, textvariable=value, width=44).grid(
                row=row, column=1, sticky="w", pady=3
            )
            values[key] = value

        ttk.Label(form, text="文档类型").grid(row=3, column=0, sticky="e", pady=3)
        kind_var = tk.StringVar(value=KIND_LABEL.get(item.get("kind") or "component", "未知"))
        ttk.Combobox(
            form,
            textvariable=kind_var,
            values=tuple(KIND_LABEL.values()),
            state="readonly",
            width=41,
        ).grid(row=3, column=1, sticky="w", pady=3)

        ttk.Label(form, text="系列产品名称").grid(
            row=4, column=0, sticky="e", pady=3
        )
        series_name_var = tk.StringVar(
            value=str(item.get("series_product_name") or "")
        )
        PlaceholderEntry(
            form,
            textvariable=series_name_var,
            placeholder="HW1.5-35(直连/水冷)",
            width=44,
        ).grid(
            row=4, column=1, sticky="w", pady=3
        )

        ttk.Label(form, text="系列产品目录").grid(
            row=5, column=0, sticky="e", pady=3
        )
        folder_value = (
            str(item.get("series_product_folder") or "").strip()
            or drawing_bundle.series_product_folder(old_code)
        )
        series_folder_var = tk.StringVar(value=folder_value)
        PlaceholderEntry(
            form,
            textvariable=series_folder_var,
            placeholder="HW系列",
            width=44,
        ).grid(
            row=5, column=1, sticky="w", pady=3
        )

        current_relation = next(
            (
                rel for rel in self.relations
                if drawing_bundle.normalize_code(rel.get("child_code")) == old_code
            ),
            {},
        )
        current_link_type = str(current_relation.get("link_type") or "")
        if not current_link_type:
            current_link_type = (
                "own" if item.get("parent_code")
                else "borrow" if item.get("borrow")
                else ""
            )
        old_parent = drawing_bundle.normalize_code(
            current_relation.get("parent_code")
            or item.get("borrow_to")
            or item.get("parent_code")
            or ""
        )
        link_type_labels = {"": "无上级关系", **LINK_TYPE_LABEL}
        link_type_var = tk.StringVar(
            value=link_type_labels.get(current_link_type, "无上级关系")
        )
        ttk.Label(form, text="上级关系").grid(
            row=6, column=0, sticky="e", pady=3
        )
        ttk.Combobox(
            form,
            textvariable=link_type_var,
            values=tuple(link_type_labels.values()),
            state="readonly",
            width=41,
        ).grid(row=6, column=1, sticky="w", pady=3)
        ttk.Label(form, text="上级图号").grid(
            row=7, column=0, sticky="e", pady=3
        )
        borrow_to_var = tk.StringVar(
            value=str(
                current_relation.get("parent_code")
                or item.get("borrow_to")
                or item.get("parent_code")
                or (item.get("machine") if item.get("borrow") else "")
                or ""
            )
        )
        ttk.Entry(form, textvariable=borrow_to_var, width=44).grid(
            row=7, column=1, sticky="w", pady=3
        )

        def save():
            new_code = drawing_bundle.normalize_code(values["code"].get())
            new_name = values["name"].get().strip()
            new_machine = drawing_bundle.normalize_code(values["machine"].get())
            new_kind = KIND_VALUE[kind_var.get()]
            new_series_name = series_name_var.get().strip()
            if new_kind != "machine_drawing":
                new_series_name = ""
                new_series_folder = ""
            else:
                new_series_folder = (
                    series_folder_var.get().strip().replace("\\", "/")
                    or drawing_bundle.series_product_folder(new_code)
                )
            makes_process = drawing_bundle.is_single_letter_process_code(new_code)
            if makes_process:
                new_name = ""
                if not new_machine:
                    new_machine = new_code[1:]
                new_kind = "process_doc"
            new_link_type = next(
                (
                    key for key, label in link_type_labels.items()
                    if label == link_type_var.get()
                ),
                "",
            )
            new_borrow = new_link_type == "borrow"
            new_parent = drawing_bundle.normalize_code(borrow_to_var.get())

            # Validate everything BEFORE touching the item, so a rejected edit
            # never leaves a half-applied row behind.
            candidate = dict(item)
            candidate.update(
                code=new_code,
                name=new_name,
                machine=new_machine,
                kind=new_kind,
                series_product_name=new_series_name,
                series_product_folder=new_series_folder,
            )
            problems = drawing_bundle.item_problems(candidate, check_content=False)
            if problems:
                messagebox.showwarning("信息不完整", "；".join(problems))
                return
            if new_link_type and not drawing_bundle.is_valid_code(new_parent):
                messagebox.showwarning(
                    "上级图号无效",
                    "选择归属或借用时，请填写有效的上级图号。",
                )
                return
            if new_borrow and new_parent.startswith("03."):
                candidate["machine"] = new_parent

            old_name = str(item.get("name") or "").strip()
            old_kind = str(item.get("kind") or "")
            old_machine = drawing_bundle.normalize_code(item.get("machine"))
            changed = (
                old_code,
                old_name,
                old_kind,
                old_machine,
                old_parent,
                current_link_type,
                str(item.get("series_product_name") or "").strip(),
                str(item.get("series_product_folder") or "").strip(),
            ) != (
                new_code,
                new_name,
                new_kind,
                candidate["machine"],
                new_parent if new_link_type else "",
                new_link_type,
                new_series_name,
                new_series_folder,
            )
            if not changed:
                dialog.destroy()
                return

            # Migrate the canonical graph: incoming (child) and outgoing
            # (parent) edges follow the renamed code; the previous parent edge
            # is dropped when it no longer applies.  Legacy item.parent_code is
            # derived from the canonical edge, never re-imported.
            if new_code and old_code and old_code != new_code:
                for relation in self.relations:
                    if drawing_bundle.normalize_code(relation.get("child_code")) == old_code:
                        relation["child_code"] = new_code
                    if drawing_bundle.normalize_code(relation.get("parent_code")) == old_code:
                        relation["parent_code"] = new_code
                for other in self.items:
                    if other is item:
                        continue
                    if drawing_bundle.normalize_code(other.get("parent_code")) == old_code:
                        other["parent_code"] = new_code
                        other["borrow_to"] = new_code
            if old_parent and (not new_link_type or new_parent != old_parent):
                self.relations = [
                    rel for rel in self.relations
                    if not (
                        drawing_bundle.normalize_code(rel.get("parent_code")) == old_parent
                        and drawing_bundle.normalize_code(rel.get("child_code")) == new_code
                    )
                ]

            item.update(
                code=new_code,
                name=new_name,
                machine=candidate["machine"],
                kind=new_kind,
                borrow=new_borrow,
                borrow_to=new_parent if new_borrow else "",
                parent_code=new_parent if new_link_type == "own" else "",
                series_product_name=new_series_name,
                series_product_folder=new_series_folder,
            )
            if new_kind == "machine_drawing":
                self._set_series_product_name(new_code, new_series_name)
            if new_link_type and new_parent:
                self._add_relation(
                    new_parent,
                    new_code,
                    link_type=new_link_type,
                    source="manual",
                    manual_override=True,
                    refresh=False,
                )
            if self.bundle is not None:
                self.bundle["relations"] = self.relations
            item["ok"] = bool(item.get("code"))
            ui_logic.invalidate_item(item, "条目已修改，需重新上传")
            self.refresh_tree()
            dialog.destroy()

        ttk.Button(form, text="保存", command=save).grid(
            row=8, column=0, columnspan=2, pady=(10, 0)
        )
        self._center_dialog_on_parent(dialog)

    def _build_settings(self) -> dict:
        return ui_logic.snapshot_settings(
            ip=self.ip_var.get(),
            port=self.port_var.get(),
            user=self.user_var.get(),
            password=self.password_var.get(),
            duplicate_policy=ui_logic.duplicate_policy_value(self.duplicate_var.get()),
            start_workflow=bool(self.workflow_var.get()),
            performer=self.performer_var.get(),
        )

    def _note_identity(self, settings: dict):
        """Another server/account invalidates every previous success state."""
        key = ui_logic.identity_key(settings)
        if self._last_identity is not None and key != self._last_identity:
            changed = ui_logic.invalidate_successes(
                self.items, "服务器/账号已变更，需重新上传"
            )
            for relation in self.relations:
                if relation.get("link_status") in ("已借用", "已归属", "已存在"):
                    relation["link_status"] = ""
                    relation["note"] = "服务器/账号已变更，需重新执行"
            if changed:
                self.log("CAXA 服务器或账号已变更，%d 项原成功状态已失效" % changed)
                self.refresh_tree()
        self._last_identity = key

    def check_bundle(self):
        if self._blocked_by_busy():
            return
        if not self.bundle:
            messagebox.showwarning("提示", "请先打开上传包或添加 PDF")
            return
        self._commit_inline_edit()
        self._ensure_batch_id()
        snapshot = ui_logic.snapshot_manifest(self.bundle)
        result = preview_bundle(snapshot)
        self.status_var.set(
            "总项 %(items)d，选择 %(selected)d，文件缺失 %(missing_files)d，"
            "命名/字段异常 %(invalid_names)d，内容校验失败 %(fingerprint_errors)d"
            % result
        )
        if not result["ready"]:
            details = list(result.get("invalid_details") or [])[:8]
            details += list(result.get("fingerprint_details") or [])[:8]
            details += [issue["message"] for issue in result.get("relation_issues") or []][:8]
            messagebox.showwarning(
                "本地检查未通过",
                self.status_var.get()
                + ("\n\n" + "\n".join(details) if details else "")
                + "\n\n可双击行修正，或点击「规范并预检」。",
            )
            return
        has_machine = bool(collect_machine_targets(self.items))
        if not self.relations and not has_machine:
            self.status_var.set("本地检查通过；没有关系或02/03工艺目标，无需连接 CAXA 预检")
            return
        try:
            settings = self._build_settings()
        except ui_logic.SettingsError as exc:
            messagebox.showwarning("检查上传包", str(exc))
            return
        self._note_identity(settings)
        if not self._save_settings():
            return
        self.set_busy(True)
        self.status_var.set("正在连接 CAXA 预检图号层级与03整机所属产品…")
        threading.Thread(
            target=self._precheck_worker,
            args=(snapshot, settings),
            daemon=True,
        ).start()

    def _precheck_worker(self, snapshot, settings):
        try:
            info = precheck_with_caxa(
                snapshot, ui_logic.account_from(settings), log=self.log
            )
            self.events.put(("precheck_done", (snapshot, info)))
        except Exception as exc:
            self.events.put(("task_error", "预检失败 %s: %s" % (type(exc).__name__, exc)))

    def start_manual_borrow(self):
        if self._blocked_by_busy():
            return
        if self._blocked_by_update():
            return
        self._ensure_batch_id()
        snapshot = ui_logic.snapshot_manifest(self.bundle)
        batch_codes = set(self._code_name_map())
        existing_only = [
            relation for relation in snapshot.get("relations") or []
            if drawing_bundle.normalize_code(relation.get("child_code"))
            not in batch_codes
            or relation.get("source") == "manual"
        ]
        if not self.manual_borrows and not existing_only:
            messagebox.showwarning("提示", "请先添加借用计划")
            return
        try:
            settings = self._build_settings()
        except ui_logic.SettingsError as exc:
            messagebox.showwarning("提示", str(exc))
            return
        self._note_identity(settings)
        if not self._save_settings():
            return
        self.set_busy(True)
        self.status_var.set("执行手动借用…")
        threading.Thread(
            target=self._manual_borrow_worker,
            args=(snapshot, settings),
            daemon=True,
        ).start()

    def _manual_borrow_worker(self, snapshot, settings):
        try:
            result = apply_manual_borrows(
                snapshot,
                ui_logic.account_from(settings),
                log=self.log,
            )
            self.events.put(("manual_done", (snapshot, result)))
        except Exception as exc:
            self.events.put(("task_error", "%s: %s" % (type(exc).__name__, exc)))

    def _save_settings(self) -> bool:
        try:
            password = self.password_var.get()
            self.cfg["caxa_server_ip"] = self.ip_var.get().strip()
            self.cfg["caxa_server_port"] = self.port_var.get().strip()
            self.cfg["caxa_user"] = self.user_var.get().strip()
            self.cfg["start_workflow_after_upload"] = bool(
                self.workflow_var.get()
            )
            self.cfg["workflow_performer"] = self.performer_var.get().strip()
            self.cfg["duplicate_policy"] = ui_logic.duplicate_policy_value(
                self.duplicate_var.get()
            )
            self.cfg["caxa_password_enc"] = config._protect(password)
            config.save(self.cfg)
            return True
        except Exception as exc:
            messagebox.showerror(
                "配置保存失败",
                "无法保存 CAXA 配置：%s" % exc,
            )
            return False

    def _workflow_options(self) -> dict:
        return {
            "template_id": int(self.cfg.get("workflow_template_id", 1)),
            "template_instance_id": int(
                self.cfg.get("workflow_template_instance_id", 31)
            ),
            "process_def_id": str(self.cfg.get("workflow_process_def_id", "2")),
            "activity_def_id": str(self.cfg.get("workflow_activity_def_id", "4")),
            "default_performer": str(self.cfg.get("workflow_performer") or ""),
        }

    def start_workflow_only(self):
        """Start the review workflow from persisted, confirmed upload results."""
        if self.busy:
            self.status_var.set("当前任务仍在进行，请等待完成")
            return
        if self._blocked_by_update():
            return
        if not self.bundle:
            messagebox.showwarning("提示", "请先打开包含已上传图纸的上传包")
            return
        selected = [
            item for item in self.items
            if bool(item.get("upload_sel", True))
            and drawing_bundle.normalize_code(item.get("code"))
        ]
        if not selected:
            messagebox.showwarning("提示", "请先勾选要发起流程的图纸")
            return
        try:
            settings = self._build_settings()
        except ui_logic.SettingsError as exc:
            messagebox.showwarning("提示", str(exc))
            return
        self._note_identity(settings)
        answer = messagebox.askyesno(
            "单独发起流程",
            "将为当前勾选的 %d 项图纸发起“审核打印”流程，审核人：%s。\n"
            "本操作只使用本机已确认成功的上传记录，不会重新上传 PDF；"
            "若图纸上传尚未确认，或 CAXA 中缺少当前计划关系，将停止发起。\n\n"
            "是否继续？" % (len(selected), settings["performer"]),
        )
        if not answer:
            return
        settings = dict(settings)
        settings["start_workflow"] = True
        if not self._save_settings():
            return
        options = self._workflow_options()
        options["workflow_only"] = True
        self._ensure_batch_id()
        snapshot = ui_logic.snapshot_manifest(self.bundle)
        self.set_busy(True)
        self.status_var.set("发起流程中…")
        threading.Thread(
            target=self._upload_worker,
            args=(snapshot, settings, options),
            daemon=True,
        ).start()

    def start_upload(self, force_new_task: bool = False):
        if self.busy:
            self.status_var.set("当前任务仍在进行，请等待完成")
            return
        if self._blocked_by_update():
            return
        if not self.bundle:
            messagebox.showwarning("提示", "请先打开上传包")
            return
        try:
            settings = self._build_settings()
        except ui_logic.SettingsError as exc:
            messagebox.showwarning("提示", str(exc))
            return
        self._note_identity(settings)
        self._commit_inline_edit()
        required_missing = sorted(
            code for code in self._series_product_required_codes
            if (
                any(item.get("upload_sel", True) for item in self._series_product_items(code))
                and not str((self._series_product_plan_item(code) or {}).get("series_product_name") or "").strip()
            )
        )
        if required_missing:
            self._select_machine_code(required_missing[0])
            self.tree.focus_set()
            messagebox.showwarning(
                "缺少系列产品名称",
                "以下03整机产品不存在，请双击清单中对应的系列产品行填写名称：\n"
                + "\n".join(required_missing[:20]),
            )
            return
        self._ensure_batch_id()
        snapshot = ui_logic.snapshot_manifest(self.bundle)
        if force_new_task:
            snapshot["force_new_task"] = True
        preview = preview_bundle(snapshot)
        if not preview["ready"]:
            details = list(preview.get("invalid_details") or [])[:8]
            details += list(preview.get("fingerprint_details") or [])[:8]
            details += [issue["message"] for issue in preview.get("relation_issues") or []][:8]
            messagebox.showerror(
                "检查未通过",
                "上传包内没有选中的图纸，或存在缺失 PDF/字段/内容校验异常。\n"
                + ("\n".join(details) if details else "")
                + "\n可点击「规范并预检」或双击行修正。",
            )
            return
        if settings["start_workflow"]:
            answer = messagebox.askyesno(
                "确认发起流程",
                "图纸上传完成后将立即发起“审核打印”流程，审核人：%s。\n"
                "本批任一项上传失败、关系未成功或结果待核实时不会发起流程。是否继续？"
                % settings["performer"],
            )
            if not answer:
                return

        if not self._save_settings():
            return
        options = self._workflow_options()
        self.set_busy(True)
        self.status_var.set("上传中…")
        threading.Thread(
            target=self._upload_worker,
            args=(snapshot, settings, options),
            daemon=True,
        ).start()

    def _upload_worker(self, snapshot, settings, options):
        """Worker thread: reads only the frozen snapshot, never Tk variables."""
        try:
            result = upload_bundle(
                snapshot,
                ui_logic.account_from(settings),
                on_duplicate=settings["duplicate_policy"],
                borrow_missing=True,
                start_workflow=settings["start_workflow"],
                workflow_only=bool(options.get("workflow_only")),
                workflow_template_id=options["template_id"],
                workflow_template_instance_id=options["template_instance_id"],
                workflow_process_def_id=options["process_def_id"],
                workflow_activity_def_id=options["activity_def_id"],
                performer_name=(
                    settings["performer"] or options.get("default_performer") or ""
                ),
                log=self.log,
                progress=lambda done, total, text: self.events.put(
                    ("status", "%s (%d/%d)" % (text, done, total))
                ),
                force_new_task=bool(snapshot.get("force_new_task")),
                auto_confirm=ui_logic.should_auto_confirm(
                    settings["duplicate_policy"]
                ),
                bundle_path=(
                    str(self.manifest_path.parent) if self.manifest_path else ""
                ),
            )
            self.events.put((
                "upload_done", (snapshot, result, settings, bool(options.get("workflow_only")))
            ))
        except Exception as exc:
            self.events.put(("task_error", "%s: %s" % (type(exc).__name__, exc)))

    def _recovery_worker(
        self, selected, settings, items_by_code, workflow_overrides=None,
        active_digest=None,
    ):
        """Verify selected pending operations without blocking the Tk loop."""
        journal = None
        verifier = None
        verifier_key = None
        successes = []
        failures = []
        workflow_overrides = workflow_overrides or {}

        def ensure_verifier(task):
            nonlocal verifier, verifier_key
            key = (
                str(task.get("server") or ""),
                int(task.get("port") or settings["port"]),
                str(task.get("login_user") or ""),
            )
            if key[0] and key[0] != settings["ip"]:
                raise RecoveryEvidenceError(
                    "待核实任务属于服务器 %s，请先切换到该服务器" % key[0]
                )
            if key[1] and key[1] != int(settings["port"]):
                raise RecoveryEvidenceError(
                    "待核实任务属于端口 %s，请先切换端口" % key[1]
                )
            if key[2] and key[2] != settings["user"]:
                raise RecoveryEvidenceError(
                    "待核实任务属于账号 %s，请先切换到该账号" % key[2]
                )
            if verifier is not None and verifier_key != key:
                verifier.close()
                verifier = None
            if verifier is None:
                self.events.put(
                    ("status", "正在连接 CAXA 回读待核实证据…")
                )
                verifier = RecoveryVerifier.connect(
                    ui_logic.account_from(settings),
                    journal,
                    log=self.log,
                )
                verifier_key = key
            return verifier

        try:
            journal = journal_module.TaskJournal()
            for task, row in selected:
                label = "%s %s" % (
                    ui_logic.recovery_kind_label(row.get("kind")),
                    ui_logic.recovery_operation_subject(row),
                )
                try:
                    active = ensure_verifier(task)
                    self.events.put(("status", "正在回读：%s" % label))
                    kind = str(row.get("kind") or "")
                    summary_row = dict(row)
                    if kind == "upload":
                        code = drawing_bundle.normalize_code(
                            row.get("item_code") or row.get("key")
                        )
                        current_item = (
                            items_by_code.get(code)
                            if active_digest and task.get("digest") == active_digest
                            else None
                        )
                        evidence = active.verify_upload(row, current_item)
                    elif kind == "relation":
                        evidence = active.verify_relation(row)
                    elif kind == "workflow":
                        process_id = str(
                            workflow_overrides.get(
                                (task["task_id"], row["key"]),
                                row.get("remote_id") or "",
                            )
                        ).strip()
                        if not process_id:
                            raise RecoveryEvidenceError(
                                "缺少流程实例 ID；请在 CAXA 中查看后重新打开"
                                "待核实窗口处理"
                            )
                        summary_row["remote_id"] = process_id
                        evidence = active.verify_workflow(row, process_id)
                    else:
                        raise RecoveryEvidenceError(
                            "不支持自动核实该操作类型"
                        )
                    summary = recovery_evidence_summary(
                        summary_row, evidence
                    )
                    journal.resolve_operation(
                        task["task_id"],
                        row["kind"],
                        row["key"],
                        result="success",
                        note="UI 自动回读核实成功：" + summary,
                        **evidence,
                    )
                    successes.append("%s：%s" % (label, summary))
                    self.log("已记录人工核实成功：%s" % label)
                except (RecoveryEvidenceError, ValueError) as exc:
                    failures.append("%s：%s" % (label, exc))
                except Exception as exc:
                    failures.append(
                        "%s：%s: %s"
                        % (label, type(exc).__name__, exc)
                    )
            self.events.put(("recovery_done", (successes, failures)))
        except Exception as exc:
            self.events.put(
                (
                    "recovery_error",
                    "%s: %s" % (type(exc).__name__, exc),
                )
            )
        finally:
            if verifier is not None:
                try:
                    verifier.close()
                except Exception:
                    pass
            if journal is not None:
                journal.close()

    def recover_pending(self):
        """Explicit, audited recovery of operations with unknown outcome.

        Confirmed successes are kept; only the addressed operation changes.
        Equivalent to app/upload_recover.py, but usable from the UI.
        """
        if self._blocked_by_busy():
            return
        journal = None
        try:
            journal = journal_module.TaskJournal()
        except Exception as exc:
            messagebox.showerror("待核实结果", "无法打开本机任务日志：%s" % exc)
            return

        try:
            tasks = [
                (task, journal.pending_operations(task["task_id"]))
                for task in journal.list_tasks_with_pending_operations()
            ]
            pending_tasks = [(task, rows) for task, rows in tasks if rows]
            if not pending_tasks:
                messagebox.showinfo("待核实结果", "没有待核实的操作。")
                return
            action, selected = choose_pending_recovery_action(
                self, pending_tasks
            )
            if action == "cancel":
                return
            if action in ("no_write", "no_write_retry"):
                active_digest = None
                if self.bundle is not None:
                    try:
                        active_digest = content_digest(self.bundle, self._build_settings())
                    except ui_logic.SettingsError:
                        pass
                all_in_current_batch = bool(active_digest and selected) and all(
                    task.get("digest") == active_digest for task, _row in selected
                )
                for task, row in selected:
                    journal.resolve_operation(
                        task["task_id"],
                        row["kind"],
                        row["key"],
                        result="no_write",
                        note="UI 人工确认未写入",
                    )
                    if active_digest and task.get("digest") == active_digest:
                        ui_logic.mark_no_write_in_plan(
                            self.items, self.relations, row
                        )
                    self.log(
                        "已记录人工确认未写入：%s %s"
                        % (
                            ui_logic.recovery_kind_label(row.get("kind")),
                            ui_logic.recovery_operation_subject(row),
                        )
                    )
                if self.bundle is not None:
                    self.bundle["items"] = self.items
                    self.bundle["relations"] = self.relations
                self.refresh_tree()
                if action == "no_write_retry" and all_in_current_batch:
                    self.status_var.set(
                        "已确认 %d 项未写入，正在回到主界面重新上传…"
                        % len(selected)
                    )
                    self.after(0, self.start_upload)
                else:
                    guidance = (
                        "现在可点击“开始上传”重试。" if all_in_current_batch
                        else "请打开对应上传批次并使用原 CAXA 账号，再点击“开始上传”。"
                    )
                    messagebox.showinfo(
                        "已确认未写入",
                        "已解除 %d 项待核实状态。%s" % (len(selected), guidance),
                    )
                    self.status_var.set("待核实已解除")
                return

            workflow_overrides = {}
            for task, row in selected:
                if (
                    str(row.get("kind") or "") == "workflow"
                    and not str(row.get("remote_id") or "").strip()
                ):
                    process_id = (
                        simpledialog.askstring(
                            "流程实例 ID",
                            "请输入 CAXA 流程列表或属性中可见的流程实例 ID：",
                            parent=self,
                        )
                        or ""
                    ).strip()
                    if process_id:
                        workflow_overrides[(task["task_id"], row["key"])] = (
                            process_id
                        )
            settings = self._build_settings()
            items_by_code = {
                drawing_bundle.normalize_code(item.get("code")): dict(item)
                for item in self.items
                if drawing_bundle.normalize_code(item.get("code"))
            }
            journal.close()
            journal = None
            self.set_busy(True)
            self.status_var.set("正在后台回读 CAXA 待核实证据…")
            threading.Thread(
                target=self._recovery_worker,
                args=(
                    selected,
                    settings,
                    items_by_code,
                    workflow_overrides,
                    content_digest(self.bundle, settings) if self.bundle else None,
                ),
                daemon=True,
            ).start()
        except Exception as exc:
            messagebox.showerror("待核实结果", "%s: %s" % (type(exc).__name__, exc))
        finally:
            if journal is not None:
                journal.close()

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")


def main():
    if "--apply-update" in sys.argv:
        raise SystemExit(update_checker.run_update_mode(sys.argv))
    app = UploadApp()
    paths = [path for path in sys.argv[1:] if os.path.isfile(path)]
    if paths:
        app.after(100, lambda: app.add_pdfs(paths))
    app.mainloop()


if __name__ == "__main__":
    main()
