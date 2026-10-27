"""Native Windows directory selection, called only on explicit user action."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shutil
import subprocess
import sys

from repolens.errors import RepoLensError


class _BrowseInfo(ctypes.Structure):
    _fields_ = [("hwndOwner", wintypes.HWND), ("pidlRoot", ctypes.c_void_p),
                ("pszDisplayName", wintypes.LPWSTR), ("lpszTitle", wintypes.LPCWSTR),
                ("ulFlags", wintypes.UINT), ("lpfn", ctypes.c_void_p),
                ("lParam", wintypes.LPARAM), ("iImage", ctypes.c_int)]


def _python_directory() -> str | None:
    """Use a GUI-subsystem Python process so the chooser is visible to the user."""
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if not pythonw.exists():
        return None
    code = (
        "import tkinter as tk; from tkinter import filedialog; "
        "root=tk.Tk(); root.withdraw(); root.attributes('-topmost', True); "
        "value=filedialog.askdirectory(title='选择要以只读方式浏览的项目目录', mustexist=True); "
        "print(value, flush=True); root.destroy()"
    )
    try:
        result = subprocess.run(
            [str(pythonw), "-c", code], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding="utf-8", errors="replace", timeout=300, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _powershell_directory() -> str | None:
    """Open an interactive dialog in a separate STA process."""
    executable = shutil.which("powershell.exe")
    if not executable:
        return None
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$dialog.Description = '选择要以只读方式浏览的项目目录'; "
        "$dialog.ShowNewFolderButton = $false; "
        "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { "
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "[Console]::WriteLine($dialog.SelectedPath) }"
    )
    try:
        result = subprocess.run(
            [executable, "-NoLogo", "-NoProfile", "-STA", "-Command", script],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", timeout=300, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def choose_directory() -> str | None:
    if os.name != "nt":
        raise RepoLensError("目录选择器仅支持 Windows，请输入项目的完整路径。")
    # Keep the modal UI out of the HTTP worker's desktop/thread context.
    # None is a real user cancellation; do not open a second modal dialog.
    if Path(sys.executable).with_name("pythonw.exe").exists():
        return _python_directory()
    if shutil.which("powershell.exe"):
        return _powershell_directory()
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.OleDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    shell.SHBrowseForFolderW.argtypes = [ctypes.POINTER(_BrowseInfo)]
    shell.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell.SHGetPathFromIDListEx.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    shell.SHGetPathFromIDListEx.restype = wintypes.BOOL
    # Each HTTP worker initializes its own STA; no process-global COM apartment.
    if ole.CoInitializeEx(None, 2) < 0:
        raise RepoLensError("无法打开目录选择器，请输入项目的完整路径。")
    item = None
    try:
        display = ctypes.create_unicode_buffer(32768)
        info = _BrowseInfo()
        info.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
        info.lpszTitle = "选择要以只读方式浏览的 Python 项目目录"
        info.ulFlags = 0x0041  # BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE
        item = shell.SHBrowseForFolderW(ctypes.byref(info))
        if not item:
            return None
        result = ctypes.create_unicode_buffer(32768)
        if not shell.SHGetPathFromIDListEx(item, result, len(result), 0):
            raise RepoLensError("请选择本地文件系统目录，或手动输入完整路径。")
        return result.value
    finally:
        if item:
            ole.CoTaskMemFree(item)
        ole.CoUninitialize()
