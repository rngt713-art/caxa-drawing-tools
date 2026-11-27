"""Local task/operation journal (SQLite, standard library only).

Semantics that matter for correctness:

* ``OP_RUNNING`` is treated as *pending*: a row left running by a crash means
  the remote write may have happened.  Nothing is auto-replayed.
* a task holds one run lock at a time, so two processes cannot both resume and
  write the same batch;
* confirmed relation rows carry the link type; a relation is only "already
  done" when the confirmed type matches the current effective type;
* successful uploads persist the remote object id, class id/name and iteration
  number, so a later phase (relation edit, workflow later) never re-uploads.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import platform
import sqlite3
import threading
import time

TASK_RUNNING = "进行中"
TASK_DONE = "已完成"
TASK_PARTIAL = "部分完成"
TASK_PENDING = "待核实"
TASK_FAILED = "失败"

OP_RUNNING = "执行中"
OP_OK = "已确认成功"
OP_FAILED = "失败"
OP_PENDING = "待核实"
OP_SKIPPED = "已跳过"
OP_UNLINKED = "已解除"

#: An orphan "running" row is as uncertain as an explicit pending row.
PENDING_STATUSES = (OP_PENDING, OP_RUNNING)

DONE_RELATION_STATUSES = ("已借用", "已归属", "已存在")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id        TEXT PRIMARY KEY,
    digest         TEXT NOT NULL,
    server         TEXT NOT NULL DEFAULT '',
    port           INTEGER NOT NULL DEFAULT 0,
    login_user     TEXT NOT NULL DEFAULT '',
    bundle_path    TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT '进行中',
    stage          TEXT NOT NULL DEFAULT '',
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_digest
    ON tasks (digest, server, port, login_user);
CREATE TABLE IF NOT EXISTS operations (
    task_id      TEXT NOT NULL,
    kind         TEXT NOT NULL,
    key          TEXT NOT NULL,
    phase        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL,
    item_code    TEXT NOT NULL DEFAULT '',
    parent_code  TEXT NOT NULL DEFAULT '',
    child_code   TEXT NOT NULL DEFAULT '',
    link_type    TEXT NOT NULL DEFAULT '',
    remote_id    TEXT NOT NULL DEFAULT '',
    class_id     TEXT NOT NULL DEFAULT '',
    iterator_no  TEXT NOT NULL DEFAULT '',
    class_name   TEXT NOT NULL DEFAULT '',
    detail       TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (task_id, kind, key)
);
CREATE TABLE IF NOT EXISTS device_state (
    device_id   TEXT NOT NULL,
    state_key   TEXT NOT NULL,
    value_json  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (device_id, state_key)
);
"""

_OPERATION_COLUMNS = ("link_type", "class_id")


class JournalBusy(Exception):
    """Another process/thread currently owns this task."""


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def default_path() -> str:
    override = os.environ.get("CAXA_UPLOAD_JOURNAL") or ""
    if override:
        return override
    base = os.environ.get("LOCALAPPDATA") or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "caxa_upload_journal", "journal.sqlite3")


def relation_key(parent_code: str, child_code: str) -> str:
    return "%s|%s" % (parent_code or "", child_code or "")


def current_device_id() -> str:
    """Stable local-device identifier without exposing the Windows MachineGuid."""
    machine_guid = ""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
            ) as key:
                machine_guid = str(
                    winreg.QueryValueEx(key, "MachineGuid")[0] or ""
                ).strip()
        except OSError:
            machine_guid = ""
    source = "|".join(
        value for value in (machine_guid, platform.node().strip()) if value
    )
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return "DEV-" + digest[:20].upper()


_STALE_EMPTY_LOCK_SECONDS = 5.0


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check for a lock holder."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return bool(ok) and code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _is_stale_lock(path: str) -> bool:
    holder = 0
    try:
        with open(path, "r", encoding="utf-8") as existing:
            holder = int((existing.read() or "0").strip() or 0)
    except (OSError, ValueError):
        holder = 0
    if holder > 0:
        return not _pid_alive(holder)
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return False
    return age >= _STALE_EMPTY_LOCK_SECONDS


def _try_remove_stale_lock(path: str) -> bool:
    if not _is_stale_lock(path):
        return False
    stale_name = "%s.stale-%d-%s" % (path, os.getpid(), os.urandom(3).hex())
    try:
        os.replace(path, stale_name)
    except OSError:
        return False
    try:
        os.remove(stale_name)
    except OSError:
        pass
    return True


class TaskJournal:
    def __init__(self, path: str | None = None):
        self.path = path or default_path()
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self.device_id = current_device_id()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA busy_timeout=5000")
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            self._conn.executescript(_SCHEMA)
            self._migrate_columns()
            self._conn.commit()

    def _migrate_columns(self):
        """Keep older journal files usable after the schema grew."""
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(operations)").fetchall()
        }
        for column in _OPERATION_COLUMNS:
            if column not in existing:
                try:
                    self._conn.execute(
                        "ALTER TABLE operations ADD COLUMN %s TEXT NOT NULL DEFAULT ''"
                        % column
                    )
                except sqlite3.DatabaseError:
                    pass

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def __enter__(self) -> "TaskJournal":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- single-run lock -------------------------------------------------
    def lock_path(self, task_id: str) -> str:
        directory = os.path.join(os.path.dirname(self.path) or ".", "locks")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, task_id + ".lock")

    @contextlib.contextmanager
    def run_lock(self, key: str):
        path = self.lock_path(key)
        handle = None
        for attempt in range(2):
            try:
                handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                # A crashed process leaves a lock behind; clear it only when the
                # recorded pid is provably gone (or an empty lock has aged out).
                if attempt == 0 and _is_stale_lock(path):
                    _try_remove_stale_lock(path)
                    continue
                raise JournalBusy("任务 %s 正在本机另一进程执行" % key)
        if handle is None:
            raise JournalBusy("任务 %s 正在本机另一进程执行" % key)
        try:
            os.write(handle, str(os.getpid()).encode("ascii"))
            os.close(handle)
        except OSError:
            try:
                os.close(handle)
            except OSError:
                pass
        try:
            yield
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def release_lock(self, key: str) -> bool:
        try:
            os.remove(self.lock_path(key))
            return True
        except OSError:
            return False

    def release_stale_locks(self) -> int:
        """Remove lock files whose recorded process no longer exists."""
        directory = os.path.join(os.path.dirname(self.path) or ".", "locks")
        removed = 0
        try:
            names = os.listdir(directory)
        except OSError:
            return 0
        for name in names:
            if not name.endswith(".lock"):
                continue
            path = os.path.join(directory, name)
            if _is_stale_lock(path) and _try_remove_stale_lock(path):
                removed += 1
        return removed

    # -- tasks -----------------------------------------------------------
    def create_task(self, *, digest: str, server: str = "", port: int = 0,
                    user: str = "", bundle_path: str = "",
                    note: str = "") -> dict:
        task_id = "T%s-%s" % (
            datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
            os.urandom(3).hex(),
        )
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (task_id, digest, server, port, login_user,"
                " bundle_path, status, stage, note, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id, digest, server or "", int(port or 0), user or "",
                    bundle_path or "", TASK_RUNNING, "plan", note or "", now, now,
                ),
            )
            self._conn.commit()
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    def find_task(self, *, digest: str, server: str = "", port: int = 0,
                  user: str = "") -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE digest=? AND server=? AND port=?"
                " AND login_user=? ORDER BY rowid DESC LIMIT 1",
                (digest, server or "", int(port or 0), user or ""),
            ).fetchone()
        return dict(row) if row else None

    def tasks_for_digest(self, *, digest: str, server: str = "", port: int = 0,
                         user: str = "") -> list[dict]:
        """All attempts for one batch content (newest first)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE digest=? AND server=? AND port=?"
                " AND login_user=? ORDER BY rowid DESC",
                (digest, server or "", int(port or 0), user or ""),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_tasks(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY rowid DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(row) for row in rows]

    def list_tasks_with_pending_operations(self) -> list[dict]:
        with self._lock:
            placeholders = ",".join("?" for _ in PENDING_STATUSES)
            rows = self._conn.execute(
                "SELECT tasks.* FROM tasks WHERE EXISTS ("
                " SELECT 1 FROM operations WHERE operations.task_id = tasks.task_id"
                " AND operations.status IN (" + placeholders + "))"
                " ORDER BY tasks.created_at DESC, tasks.task_id DESC",
                tuple(PENDING_STATUSES),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_task_status(self, task_id: str, status: str, stage: str = "",
                        note: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status=?, stage=?, note=?, updated_at=?"
                " WHERE task_id=?",
                (status, stage or "", note or "", _now(), task_id),
            )
            self._conn.commit()

    def set_task_stage(self, task_id: str, stage: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET stage=?, updated_at=? WHERE task_id=?",
                (stage or "", _now(), task_id),
            )
            self._conn.commit()

    # -- device-local state ----------------------------------------------
    def get_device_state(self, state_key: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM device_state WHERE device_id=? AND state_key=?",
                (self.device_id, state_key or ""),
            ).fetchone()
        if not row:
            return None
        record = dict(row)
        try:
            value = json.loads(record.get("value_json") or "{}")
        except (TypeError, ValueError):
            value = {}
        if not isinstance(value, dict):
            value = {"value": value}
        value.update(
            {
                "device_id": record.get("device_id") or "",
                "state_key": record.get("state_key") or "",
                "created_at": record.get("created_at") or "",
                "updated_at": record.get("updated_at") or "",
            }
        )
        return value

    def set_device_state(self, state_key: str, value: dict) -> dict:
        now = _now()
        payload = json.dumps(value or {}, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._conn.execute(
                "INSERT INTO device_state"
                " (device_id, state_key, value_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(device_id, state_key) DO UPDATE SET"
                " value_json=excluded.value_json, updated_at=excluded.updated_at",
                (self.device_id, state_key or "", payload, now, now),
            )
            self._conn.commit()
        return self.get_device_state(state_key) or {}

    def plm_com_registration(self) -> dict | None:
        state = self.get_device_state("plm_com_registration")
        if state and state.get("status") == "registered" and state.get(
            "registration_id"
        ):
            return state
        return None

    def save_plm_com_registration(
        self,
        registration_id: str,
        *,
        dll_path: str = "",
        exit_code: int | None = None,
        helper_version: str = "",
        detail: dict | None = None,
    ) -> dict:
        return self.set_device_state(
            "plm_com_registration",
            {
                "status": "registered",
                "registration_id": registration_id,
                "registered_at": _now(),
                "dll_path": dll_path or "",
                "exit_code": exit_code,
                "helper_version": helper_version or "",
                "detail": detail or {},
            },
        )

    # -- operations ------------------------------------------------------
    def record_operation(self, task_id: str, kind: str, key: str, *,
                         status: str, phase: str = "", item_code: str = "",
                         parent_code: str = "", child_code: str = "",
                         link_type: str = "",
                         remote_id: str | int | None = "",
                         class_id: str | int | None = "",
                         iterator_no: str = "", class_name: str = "",
                         detail: str = "", error: str = "") -> dict:
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO operations (task_id, kind, key, phase, status,"
                " item_code, parent_code, child_code, link_type, remote_id,"
                " class_id, iterator_no, class_name, detail, error, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(task_id, kind, key) DO UPDATE SET"
                " phase=excluded.phase, status=excluded.status,"
                " item_code=excluded.item_code, parent_code=excluded.parent_code,"
                " child_code=excluded.child_code, link_type=excluded.link_type,"
                " remote_id=excluded.remote_id, class_id=excluded.class_id,"
                " iterator_no=excluded.iterator_no, class_name=excluded.class_name,"
                " detail=excluded.detail, error=excluded.error,"
                " updated_at=excluded.updated_at",
                (
                    task_id, kind, key, phase or "", status, item_code or "",
                    parent_code or "", child_code or "", link_type or "",
                    "" if remote_id is None else str(remote_id),
                    "" if class_id is None else str(class_id),
                    iterator_no or "", class_name or "", detail or "", error or "",
                    now,
                ),
            )
            self._conn.commit()
        return self.get_operation(task_id, kind, key) or {}

    def get_operation(self, task_id: str, kind: str, key: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM operations WHERE task_id=? AND kind=? AND key=?",
                (task_id, kind, key),
            ).fetchone()
        return dict(row) if row else None

    def operations(self, task_id: str, kind: str | None = None) -> list[dict]:
        sql = "SELECT * FROM operations WHERE task_id=?"
        args: list = [task_id]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        sql += " ORDER BY rowid"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def pending_operations(self, task_id: str) -> list[dict]:
        """Rows whose remote effect is unknown (including orphan running)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM operations WHERE task_id=? AND status IN (%s)"
                " ORDER BY rowid" % ",".join("?" for _ in PENDING_STATUSES),
                (task_id, *PENDING_STATUSES),
            ).fetchall()
        return [dict(row) for row in rows]

    def confirmed_uploads(self, task_id: str) -> dict[str, dict]:
        return {
            row["item_code"]: row
            for row in self.operations(task_id, "upload")
            if row["status"] == OP_OK and row["item_code"]
        }

    def confirmed_relations(self, task_id: str) -> dict[tuple[str, str], dict]:
        """Confirmed relation executions keyed by (parent, child).

        Skipped rows are not evidence of success and are excluded.
        """
        done: dict[tuple[str, str], dict] = {}
        for row in self.operations(task_id, "relation"):
            if row["status"] == OP_OK:
                done[(row["parent_code"], row["child_code"])] = row
        return done

    def relation_decisions(self, task_id: str) -> dict[tuple[str, str], str]:
        """Own/borrow decisions snapshotted before any remote write."""
        decisions: dict[tuple[str, str], str] = {}
        for row in self.operations(task_id, "decision"):
            if row["status"] == OP_OK and row["link_type"] in ("own", "borrow"):
                decisions[(row["parent_code"], row["child_code"])] = row["link_type"]
        return decisions

    # -- workflow --------------------------------------------------------
    def record_workflow(self, task_id: str, *, status: str, process_id: str | int | None,
                        stage: str = "", drawing_ids: list[int] | None = None,
                        started: bool = False, detail: str = "",
                        error: str = "", key: str = "workflow") -> dict:
        payload = {
            "drawing_ids": [int(v) for v in (drawing_ids or [])],
            "stage": stage or "",
            "started": bool(started),
        }
        if detail:
            payload["note"] = detail
        return self.record_operation(
            task_id,
            "workflow",
            key,
            status=status,
            phase=stage or "workflow",
            remote_id=process_id,
            class_name="workflow",
            detail=json.dumps(payload, ensure_ascii=False),
            error=error or "",
        )

    def workflow(self, task_id: str) -> dict | None:
        return self.get_operation(task_id, "workflow", "workflow")

    # -- explicit human recovery ----------------------------------------
    def resolve_operation(self, task_id: str, kind: str, key: str, *,
                          result: str, remote_id: str | int | None = None,
                          class_id: str | int | None = None,
                          iterator_no: str = "",
                          link_type: str = "",
                          note: str = "", process_state: str | int | None = None,
                          linked_document_ids: list[int] | None = None) -> dict:
        """Apply an audited human decision to exactly one pending operation.

        ``result`` is ``success`` (remote write verified) or ``no_write``
        (verified that nothing was written, safe to retry).
        """
        current = self.get_operation(task_id, kind, key)
        if current is None:
            raise ValueError("找不到操作 %s/%s" % (kind, key))
        if current["status"] not in (OP_PENDING, OP_RUNNING):
            raise ValueError("操作 %s/%s 不是待核实状态" % (kind, key))
        if result == "success":
            status = OP_OK
        elif result == "no_write":
            status = OP_FAILED
        else:
            raise ValueError("未知的核实结果: %s" % result)
        record = dict(current)
        # Empty fields mean "keep the recorded evidence", never erase known IDs.
        for field, value in (("remote_id", remote_id), ("class_id", class_id),
                             ("iterator_no", iterator_no), ("link_type", link_type)):
            if value is not None and str(value).strip():
                record[field] = str(value).strip()
        try:
            payload = json.loads(current.get("detail") or "{}")
        except (TypeError, ValueError):
            payload = {"previous_detail": current.get("detail") or ""}
        if not isinstance(payload, dict):
            payload = {"previous_detail": payload}

        def positive_id(value, label):
            text = str(value or "").strip()
            if not text.isascii() or not text.isdecimal() or int(text) <= 0:
                raise ValueError("%s必须是正整数，请在 CAXA 中核实后填写" % label)
            return int(text)

        if result == "success":
            if kind == "upload":
                record["remote_id"] = str(positive_id(record.get("remote_id"), "图纸对象 ID"))
                actual_class = positive_id(record.get("class_id"), "文档类型 ID")
                if actual_class not in (1002, 1003):
                    raise ValueError("文档类型只能是 Drawing=1002 或 CappDocument=1003")
                known_class = str(current.get("class_id") or "").strip()
                code = str(current.get("item_code") or key).strip()
                expected_class = 1003 if code[:1].isalpha() else 1002 if code[:1].isdigit() else None
                if (known_class in ("1002", "1003") and actual_class != int(known_class)) or (
                        expected_class is not None and actual_class != expected_class):
                    raise ValueError("文档类型与该图纸/工艺文档的已知类型不一致")
                record["class_id"] = str(actual_class)
                if not str(record.get("iterator_no") or "").strip():
                    raise ValueError("请填写在 CAXA 中核实的实际版次号")
                record["class_name"] = "Drawing" if actual_class == 1002 else "CappDocument"
            elif kind == "relation":
                actual_type = record.get("link_type")
                if actual_type not in ("own", "borrow"):
                    raise ValueError("请确认关系类型为 own（归属）或 borrow（借用）")
                expected_type = current.get("link_type") or self.relation_decisions(task_id).get(
                    (current.get("parent_code"), current.get("child_code")))
                if expected_type and actual_type != expected_type:
                    raise ValueError("核实的关系类型与计划归属/借用类型不一致")
            elif kind == "workflow":
                record["remote_id"] = str(positive_id(record.get("remote_id"), "流程实例 ID"))
                state = str(payload.get("process_state") or ""
                            if process_state is None else process_state).strip()
                if state != "2":
                    raise ValueError("必须核实流程当前状态为 2（运行中），其他状态请保留待核实")
                expected_ids = payload.get("drawing_ids")
                if not isinstance(expected_ids, list) or not expected_ids:
                    raise ValueError("日志缺少本流程应关联的文档集合，不能确认成功")
                expected = {positive_id(v, "计划文档 ID") for v in expected_ids}
                observed_ids = (payload.get("linked_document_ids")
                                if linked_document_ids is None else linked_document_ids)
                if not isinstance(observed_ids, list) or not observed_ids:
                    raise ValueError("请填写从 CAXA 流程中核实的全部关联文档 ID")
                observed = {positive_id(v, "关联文档 ID") for v in observed_ids}
                if expected != observed:
                    raise ValueError("流程实际关联文档与计划文档集合不一致，不能确认成功")
                payload.update(started=True, process_state=state, state_verified=True,
                               documents_verified=True, linked_document_ids=sorted(observed))
            else:
                raise ValueError("不支持核实此操作类型：%s" % kind)
        if note:
            payload["recovery_note"] = note
        record["status"] = status
        record["detail"] = json.dumps(payload, ensure_ascii=False)
        self.record_operation(
            task_id,
            kind,
            key,
            status=status,
            phase=record.get("phase") or "",
            item_code=record.get("item_code") or "",
            parent_code=record.get("parent_code") or "",
            child_code=record.get("child_code") or "",
            link_type=record.get("link_type") or "",
            remote_id=record.get("remote_id") or "",
            class_id=record.get("class_id") or "",
            iterator_no=record.get("iterator_no") or "",
            class_name=record.get("class_name") or "",
            detail=record.get("detail") or "",
            error="" if status == OP_OK else (note or "人工确认未写入"),
        )
        remaining = self.pending_operations(task_id)
        if not remaining:
            self.set_task_status(task_id, TASK_PARTIAL, stage="recovered")
        return self.get_operation(task_id, kind, key) or {}

    def task_summary(self, task_id: str) -> dict:
        task = self.get_task(task_id) or {}
        return {
            "task": task,
            "uploads": self.operations(task_id, "upload"),
            "relations": self.operations(task_id, "relation"),
            "decisions": self.operations(task_id, "decision"),
            "workflow": self.workflow(task_id),
            "pending": self.pending_operations(task_id),
        }
