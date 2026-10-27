"""Windows Credential Manager wrapper. Keys never enter configuration files."""

import ctypes
from ctypes import wintypes
import os

from repolens.errors import RepoLensError
from repolens.llm import validate_key

TARGET = "RepoLens/DeepSeek"
CRED_TYPE_GENERIC = 1
ERROR_NOT_FOUND = 1168


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]


def _library():
    if os.name != "nt":
        raise RepoLensError("持久保存仅支持 Windows 凭据管理器；可取消保存，仅本次使用。")
    library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    pointer = ctypes.POINTER(_Credential)
    library.CredWriteW.argtypes = [pointer, wintypes.DWORD]
    library.CredWriteW.restype = wintypes.BOOL
    library.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(pointer)]
    library.CredReadW.restype = wintypes.BOOL
    library.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    library.CredDeleteW.restype = wintypes.BOOL
    library.CredFree.argtypes = [ctypes.c_void_p]
    library.CredFree.restype = None
    return library


def save_key(key: str) -> None:
    raw = validate_key(key).encode("utf-8")
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    credential = _Credential()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = TARGET
    credential.CredentialBlobSize = len(raw)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE: only this Windows user.
    credential.UserName = "DeepSeek"
    if not _library().CredWriteW(ctypes.byref(credential), 0):
        raise RepoLensError("Windows 安全保存失败；可取消保存，仅本次使用。", 500)


def load_key() -> str | None:
    if os.name != "nt":
        return None
    library = _library()
    pointer = ctypes.POINTER(_Credential)()
    if not library.CredReadW(TARGET, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        if ctypes.get_last_error() == ERROR_NOT_FOUND:
            return None
        raise RepoLensError("无法读取 Windows 凭据，请稍后重试。", 500)
    try:
        raw = ctypes.string_at(pointer.contents.CredentialBlob, pointer.contents.CredentialBlobSize)
        return validate_key(raw.decode("utf-8"))
    finally:
        library.CredFree(pointer)


def delete_key() -> None:
    if os.name != "nt":
        return
    if not _library().CredDeleteW(TARGET, CRED_TYPE_GENERIC, 0) and ctypes.get_last_error() != ERROR_NOT_FOUND:
        raise RepoLensError("无法删除 Windows 凭据，请在凭据管理器中检查 RepoLens/DeepSeek。", 500)
