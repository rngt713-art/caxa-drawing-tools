"""GitHub Release update checks and downloads for the uploader."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import BinaryIO, Callable, Mapping


REPOSITORY = "rngt713-art/caxa-drawing-tools"
LATEST_RELEASE_API = (
    "https://api.github.com/repos/%s/releases/latest" % REPOSITORY
)
RELEASES_URL = "https://github.com/%s/releases" % REPOSITORY
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
ASSET_PATTERN = re.compile(
    r"^CAXA-Drawing-Uploader-v(\d+\.\d+\.\d+)\.exe$", re.IGNORECASE
)
SHA256_PATTERN = re.compile(r"^sha256:([0-9a-fA-F]{64})$")


class UpdateError(RuntimeError):
    """A recoverable update-check or download error."""


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    available: bool
    tag_name: str
    asset_name: str = ""
    download_url: str = ""
    sha256: str = ""
    release_url: str = ""
    published_at: str = ""

    def as_state(self) -> dict:
        return asdict(self)

    @classmethod
    def from_state(cls, current_version: str, state: Mapping) -> "UpdateInfo | None":
        latest = str(state.get("latest_version") or "")
        if not latest or compare_versions(latest, current_version) <= 0:
            return None
        required = ("asset_name", "download_url", "sha256")
        if any(not str(state.get(key) or "") for key in required):
            return None
        return cls(
            current_version=current_version,
            latest_version=latest,
            available=True,
            tag_name=str(state.get("tag_name") or "v%s" % latest),
            asset_name=str(state.get("asset_name") or ""),
            download_url=str(state.get("download_url") or ""),
            sha256=str(state.get("sha256") or ""),
            release_url=str(state.get("release_url") or RELEASES_URL),
            published_at=str(state.get("published_at") or ""),
        )


def parse_version(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(str(value or "").strip())
    if not match:
        raise UpdateError("无效版本号：%s" % value)
    return tuple(int(part) for part in match.groups())


def compare_versions(left: str, right: str) -> int:
    left_parts = parse_version(left)
    right_parts = parse_version(right)
    return (left_parts > right_parts) - (left_parts < right_parts)


def _request_headers(current_version: str, token: str = "") -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "CAXA-Drawing-Uploader/%s" % current_version,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = "Bearer %s" % token
    return headers


def _default_token() -> str:
    return (
        os.environ.get("GITHUB_TOKEN", "").strip()
        or os.environ.get("GH_TOKEN", "").strip()
    )


def _select_release_asset(
    assets: list[Mapping], latest_version: str
) -> Mapping | None:
    exact_name = "CAXA-Drawing-Uploader-v%s.exe" % latest_version
    for asset in assets:
        if str(asset.get("name") or "").lower() == exact_name.lower():
            return asset
    for asset in assets:
        match = ASSET_PATTERN.fullmatch(str(asset.get("name") or ""))
        if match and match.group(1) == latest_version:
            return asset
    return None


def check_for_update(
    current_version: str,
    *,
    opener: Callable = urllib.request.urlopen,
    timeout: float = 12.0,
    token: str | None = None,
) -> UpdateInfo:
    """Return the latest stable release and whether it is newer."""
    token = _default_token() if token is None else token
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers=_request_headers(current_version, token),
    )
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("GitHub 上没有可用的正式 Release") from exc
        raise UpdateError("GitHub 返回 HTTP %s" % exc.code) from exc
    except (OSError, ValueError) as exc:
        raise UpdateError("无法连接 GitHub：%s" % exc) from exc

    tag_name = str(payload.get("tag_name") or "")
    try:
        latest_version = "%d.%d.%d" % parse_version(tag_name)
    except UpdateError as exc:
        raise UpdateError("最新 Release 的 tag 不是 vX.Y.Z：%s" % tag_name) from exc
    available = compare_versions(latest_version, current_version) > 0
    asset = _select_release_asset(list(payload.get("assets") or []), latest_version)
    asset_name = str((asset or {}).get("name") or "")
    download_url = str((asset or {}).get("browser_download_url") or "")
    digest_match = SHA256_PATTERN.fullmatch(
        str((asset or {}).get("digest") or "")
    )
    sha256 = digest_match.group(1).lower() if digest_match else ""

    if available and not asset:
        raise UpdateError(
            "Release v%s 缺少 CAXA-Drawing-Uploader-v%s.exe"
            % (latest_version, latest_version)
        )
    if available and not sha256:
        raise UpdateError(
            "Release v%s 的 EXE 缺少 GitHub SHA-256 digest，已阻止下载"
            % latest_version
        )
    return UpdateInfo(
        current_version=current_version,
        latest_version=latest_version,
        available=available,
        tag_name=tag_name,
        asset_name=asset_name,
        download_url=download_url,
        sha256=sha256,
        release_url=str(payload.get("html_url") or RELEASES_URL),
        published_at=str(payload.get("published_at") or ""),
    )


def _is_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".update-", delete=True):
            return True
    except OSError:
        return False


def default_download_directory() -> Path:
    downloads = Path.home() / "Downloads"
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        if _is_writable_directory(executable_dir):
            return executable_dir
    if _is_writable_directory(downloads):
        return downloads
    fallback = Path(tempfile.gettempdir()) / "CAXA-Drawing-Uploader"
    _is_writable_directory(fallback)
    return fallback


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_response(
    opener: Callable, url: str, current_version: str, timeout: float
) -> BinaryIO:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "CAXA-Drawing-Uploader/%s" % current_version,
        },
    )
    return opener(request, timeout=timeout)


def download_update(
    info: UpdateInfo,
    *,
    target_dir: str | os.PathLike | None = None,
    opener: Callable = urllib.request.urlopen,
    timeout: float = 60.0,
) -> Path:
    """Download a newer version and verify the GitHub-provided SHA-256."""
    if not info.available:
        raise UpdateError("当前版本已是最新，无需下载")
    if not info.download_url or not info.sha256:
        raise UpdateError("更新信息不完整，已阻止下载")
    if not ASSET_PATTERN.fullmatch(info.asset_name):
        raise UpdateError("更新文件名不安全：%s" % info.asset_name)

    directory = (
        Path(target_dir).expanduser().resolve()
        if target_dir
        else default_download_directory()
    )
    if not _is_writable_directory(directory):
        raise UpdateError("下载目录不可写：%s" % directory)

    target = directory / info.asset_name
    if target.exists():
        if _sha256_file(target).lower() == info.sha256.lower():
            return target
        suffix = 1
        while True:
            candidate = target.with_name(
                "%s (%d)%s" % (target.stem, suffix, target.suffix)
            )
            if not candidate.exists():
                target = candidate
                break
            suffix += 1

    fd, temporary_name = tempfile.mkstemp(
        prefix=".%s-" % info.asset_name,
        suffix=".part",
        dir=directory,
    )
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as output:
            try:
                with _download_response(
                    opener, info.download_url, info.current_version, timeout
                ) as response:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        digest.update(chunk)
            except urllib.error.HTTPError as exc:
                raise UpdateError("下载更新失败：GitHub 返回 HTTP %s" % exc.code) from exc
            except OSError as exc:
                raise UpdateError("下载更新失败：%s" % exc) from exc
            output.flush()
            os.fsync(output.fileno())
        actual = digest.hexdigest().lower()
        if actual != info.sha256.lower():
            raise UpdateError(
                "更新文件 SHA-256 不匹配，已删除下载文件"
            )
        os.replace(temporary, target)
        return target
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def wait_for_process_exit(pid: int, timeout: float = 120.0) -> None:
    if os.name != "nt":
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.2)
        raise UpdateError("等待旧版本退出超时")

    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(synchronize, False, int(pid))
    if not handle:
        return
    try:
        result = kernel32.WaitForSingleObject(
            handle, max(0, int(timeout * 1000))
        )
        if result == wait_timeout:
            raise UpdateError("等待旧版本退出超时")
        if result != wait_object_0:
            raise UpdateError("等待旧版本退出失败（结果 %s）" % result)
    finally:
        kernel32.CloseHandle(handle)


def _unique_backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = path.with_name("%s.old-%s" % (path.name, stamp))
    if not base.exists():
        return base
    for index in range(1, 1000):
        candidate = base.with_name("%s-%d" % (base.name, index))
        if not candidate.exists():
            return candidate
    raise UpdateError("无法为旧版本创建唯一备份名")


def apply_update(
    old_path: str | os.PathLike,
    parent_pid: int,
    *,
    source_exe: str | os.PathLike | None = None,
    wait_exit: Callable = wait_for_process_exit,
    popen: Callable = subprocess.Popen,
) -> tuple[object, Path]:
    """Replace the old EXE with the running update-mode EXE and relaunch it."""
    wait_exit(parent_pid)
    old = Path(old_path).expanduser().resolve()
    source = Path(source_exe or sys.executable).expanduser().resolve()
    if old == source:
        raise UpdateError("更新源与目标路径相同，已阻止覆盖")
    if not source.is_file():
        raise UpdateError("更新源文件不存在：%s" % source)
    old.parent.mkdir(parents=True, exist_ok=True)

    backup = _unique_backup_path(old) if old.exists() else None
    temporary = old.with_name(
        ".%s.update-%s.tmp" % (old.name, os.getpid())
    )
    moved_old = False
    try:
        if backup is not None:
            os.replace(old, backup)
            moved_old = True
        shutil.copy2(source, temporary)
        os.replace(temporary, old)
        if _sha256_file(old) != _sha256_file(source):
            raise UpdateError("覆盖后的 EXE 校验失败")
        process = popen([str(old)], cwd=str(old.parent))
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        if moved_old and backup is not None:
            try:
                if old.exists():
                    old.unlink()
                os.replace(backup, old)
            except OSError:
                pass
        raise
    return process, backup or old


def launch_update(
    path: str | os.PathLike,
    *,
    install_target: str | os.PathLike | None = None,
    parent_pid: int | None = None,
    popen: Callable = subprocess.Popen,
    sleep: Callable = time.sleep,
    startup_wait: float = 1.2,
):
    """Start the downloaded EXE, optionally in overwrite-and-relaunch mode."""
    executable = Path(path).expanduser().resolve()
    if not executable.is_file():
        raise UpdateError("更新文件不存在：%s" % executable)
    arguments = [str(executable)]
    if install_target is not None and parent_pid is not None:
        arguments.extend(
            [
                "--apply-update",
                str(Path(install_target).expanduser().resolve()),
                str(int(parent_pid)),
            ]
        )
    try:
        process = popen(arguments, cwd=str(executable.parent))
    except OSError as exc:
        raise UpdateError("无法启动更新文件：%s" % exc) from exc
    sleep(startup_wait)
    exit_code = process.poll()
    if exit_code is not None:
        raise UpdateError(
            "新版本启动后立即退出（退出码 %s）" % exit_code
        )
    return process


def run_update_mode(argv: list[str]) -> int:
    try:
        index = argv.index("--apply-update")
        old_path = argv[index + 1]
        parent_pid = int(argv[index + 2])
        apply_update(old_path, parent_pid)
        return 0
    except Exception as exc:
        try:
            base = (
                os.environ.get("LOCALAPPDATA")
                or os.path.join(str(Path.home()), "AppData", "Local")
            )
            log_path = Path(base) / "caxa_upload_journal" / "update_error.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    "%s %s: %s\n"
                    % (
                        datetime.now().isoformat(timespec="seconds"),
                        type(exc).__name__,
                        exc,
                    )
                )
        except OSError:
            pass
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                "自动覆盖旧版本失败：\n%s" % exc,
                "CAXA 图纸上传助手更新失败",
                0x10,
            )
        except Exception:
            pass
        return 1


class UpdateStateStore:
    """Persist the once-per-day check and the last known release."""

    def __init__(self, path: str | os.PathLike | None = None):
        if path is None:
            override = os.environ.get("CAXA_UPDATE_STATE_PATH", "").strip()
            if override:
                path = override
            else:
                base = (
                    os.environ.get("LOCALAPPDATA")
                    or os.path.join(str(Path.home()), "AppData", "Local")
                )
                path = os.path.join(
                    base, "caxa_upload_journal", "update_state.json"
                )
        self.path = Path(path)
        self._state = self._load()

    def _load(self) -> dict:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=".update-state-",
            suffix=".tmp",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(self._state, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        except Exception:
            try:
                os.remove(temporary_name)
            except OSError:
                pass
            raise

    def checked_today(self, today: date | None = None) -> bool:
        return str(self._state.get("last_check_date") or "") == (
            today or date.today()
        ).isoformat()

    def cached_update(self, current_version: str) -> UpdateInfo | None:
        return UpdateInfo.from_state(current_version, self._state)

    def mark_attempt(
        self,
        *,
        success: bool,
        info: UpdateInfo | None = None,
        error: str = "",
        today: date | None = None,
    ) -> None:
        check_date = (today or date.today()).isoformat()
        state = dict(self._state)
        state["last_check_date"] = check_date
        state["last_check_success"] = bool(success)
        state["last_check_error"] = str(error or "")
        if info is not None:
            state.update(info.as_state())
            state.pop("downloaded_path", None)
            state.pop("downloaded_version", None)
        self._state = state
        self._save()
