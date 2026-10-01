#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
① 一键提取 · 01_extract.py
════════════════════════════════════════════════════════════
在「装了 Qoder 客户端」的 Windows 机器上跑，一次同时取出并自动写回：
  · 设备标识（Cosy-*）→ 写入 config.json 的 "device" 块
  · token + refreshToken → 写入 accounts.json 的 accounts 数组（按 uid/refreshToken 去重，新增则追加）

设备标识用客户端自带 runtime-info.exe 生成；token 用 DPAPI 解密加密密钥
再 AES-GCM 解密 auth.v1.dat 得到（解密需「登录时的同一 Windows 用户」）。
零第三方依赖：优先用 cryptography，未安装则退回 Windows CNG（bcrypt）。

用法：
    python 01_extract.py             # 全取并自动写回（默认）
    python 01_extract.py --device    # 只取设备标识
    python 01_extract.py --token     # 只取 token
    python 01_extract.py --json      # 只输出 JSON，不写文件
    python 01_extract.py --no-write  # 只打印预览，不写文件（同 --dry-run）

环境变量：
    QODER_AUTH_DIR   手动指定客户端数据目录（auth.v1.dat / auth.machine-id 所在）
    QODER_UMID_EXE   手动指定 runtime-info.exe 全路径（找不到时用）
    QODER_CONFIG_FILE    写回的 config.json 路径（默认本目录）
    QODER_ACCOUNTS_FILE  写回的 accounts.json 路径（默认本目录）
"""
import base64
import ctypes
import ctypes.wintypes as wt
import glob
import json
import os
import platform
import socket
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

UMID_REL = ("resources", "umid", "runtime-info.exe")

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.getenv("QODER_CONFIG_FILE", "").strip() or os.path.join(HERE, "config.json")
ACCOUNTS_FILE = os.getenv("QODER_ACCOUNTS_FILE", "").strip() or os.path.join(HERE, "accounts.json")


# ── Windows 解密原语（DPAPI + AES-GCM）────────────────────
class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


class _AeadInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.ULONG), ("dwInfoVersion", wt.ULONG),
        ("pbNonce", ctypes.POINTER(ctypes.c_ubyte)), ("cbNonce", wt.ULONG),
        ("pbAuthData", ctypes.POINTER(ctypes.c_ubyte)), ("cbAuthData", wt.ULONG),
        ("pbTag", ctypes.POINTER(ctypes.c_ubyte)), ("cbTag", wt.ULONG),
        ("pbMacContext", ctypes.POINTER(ctypes.c_ubyte)), ("cbMacContext", wt.ULONG),
        ("cbAAD", wt.ULONG), ("cbData", ctypes.c_ulonglong), ("dwFlags", wt.ULONG),
    ]


def _dpapi_unprotect(data):
    src = _DataBlob(len(data), ctypes.create_string_buffer(data, len(data)))
    dst = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0x01, ctypes.byref(dst)):
        raise OSError("DPAPI 解不开（%s）——需同一用户且已登录桌面" % ctypes.WinError())
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def _decrypt_gcm_cng(key, nonce, blob):
    bcrypt = ctypes.WinDLL("bcrypt.dll")

    def chk(res, what):
        if res != 0:
            raise OSError("CNG %s 失败 NTSTATUS=0x%08x" % (what, res & 0xFFFFFFFF))

    ct, tag = blob[:-16], blob[-16:]
    h_alg = ctypes.c_void_p()
    chk(bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), "AES", None, 0), "OpenAlg")
    mode = "ChainingModeGCM"
    chk(bcrypt.BCryptSetProperty(h_alg, "ChainingMode", mode, (len(mode) + 1) * 2, 0), "SetProperty")
    h_key = ctypes.c_void_p()
    kbuf = ctypes.create_string_buffer(key, len(key))
    chk(bcrypt.BCryptGenerateSymmetricKey(h_alg, ctypes.byref(h_key), None, 0, kbuf, len(key), 0), "GenKey")
    nbuf = (ctypes.c_ubyte * len(nonce)).from_buffer_copy(nonce)
    tbuf = (ctypes.c_ubyte * len(tag)).from_buffer_copy(tag)
    info = _AeadInfo()
    info.cbSize = ctypes.sizeof(_AeadInfo)
    info.dwInfoVersion = 1
    info.pbNonce = ctypes.cast(nbuf, ctypes.POINTER(ctypes.c_ubyte))
    info.cbNonce = len(nonce)
    info.pbTag = ctypes.cast(tbuf, ctypes.POINTER(ctypes.c_ubyte))
    info.cbTag = len(tag)
    ibuf = ctypes.create_string_buffer(ct, len(ct))
    obuf = ctypes.create_string_buffer(len(ct))
    done = ctypes.c_ulong()
    chk(bcrypt.BCryptDecrypt(h_key, ibuf, len(ct), ctypes.byref(info), None, 0,
                             obuf, len(ct), ctypes.byref(done), 0), "Decrypt")
    return obuf.raw[:done.value]


def _decrypt_gcm(key, nonce, blob):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        return _decrypt_gcm_cng(key, nonce, blob)
    return AESGCM(key).decrypt(nonce, blob, None)


# ── 客户端数据目录 / 安装目录 ──────────────────────────────
def appdata_dirs():
    """客户端数据目录：国际版 com.qoder.app.* / 国内版 com.qodercn.app.* 都找。"""
    env = os.environ.get("QODER_AUTH_DIR", "").strip()
    if env:
        return [env]
    appdata = os.environ.get("APPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Roaming"))
    out = []
    for pat in ("com.qoder.app.*", "com.qodercn.app.*"):
        out += sorted(glob.glob(os.path.join(appdata, pat)), key=os.path.getmtime, reverse=True)
    seen, res = set(), []
    for p in out:
        k = p.lower()
        if k not in seen:
            seen.add(k)
            res.append(p)
    return res


def reg_roots():
    import winreg
    roots = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            key = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")
        except OSError:
            continue
        try:
            n = winreg.QueryInfoKey(key)[0]
        except OSError:
            winreg.CloseKey(key)
            continue
        for i in range(n):
            try:
                with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                    name = str(winreg.QueryValueEx(sub, "DisplayName")[0]).lower()
                    if "qoder" in name:
                        loc = str(winreg.QueryValueEx(sub, "InstallLocation")[0]).strip()
                        if loc:
                            roots.append(os.path.abspath(loc))
            except OSError:
                continue
        winreg.CloseKey(key)
    return roots


def candidate_umid_exes():
    env = os.environ.get("QODER_UMID_EXE", "").strip()
    if env:
        return [env]
    local = os.environ.get("LOCALAPPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Local"))
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    roots = [os.path.join(local, "Programs", "Qoder"), os.path.join(pf, "Qoder"), os.path.join(pf86, "Qoder"),
             os.path.join(local, "Programs", "Qoder CN"), os.path.join(pf, "Qoder CN"), os.path.join(pf86, "Qoder CN")]
    roots += reg_roots()
    return [os.path.join(r, *UMID_REL) for r in roots]


def run_umid(exe):
    try:
        p = subprocess.run([exe, "--account-stdin"], input=b"", capture_output=True,
                           timeout=40, cwd=os.path.dirname(exe))
        out = p.stdout.decode("utf-8", "replace").strip()
        lines = [l for l in out.splitlines() if l.strip()]
        return json.loads(lines[-1]) if lines else {}
    except Exception:
        return {}


def arch():
    m = platform.machine().lower()
    return {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(m, "x86_64")


# ── 取设备标识 ───────────────────────────────────────────
def get_device():
    exe = next((p for p in candidate_umid_exes() if os.path.isfile(p)), None)
    ri = run_umid(exe) if exe else {}
    dev = {
        "clientType": "10",
        "machineOS": arch() + "_windows",
        "machineHostname": socket.gethostname(),
        "machineId": "",
        "machineToken": ri.get("machineToken") or "",
        "machineCode": ri.get("machineCode") or "",
        "machineType": ri.get("machineType") or "",
        "version": "",
    }
    for root in appdata_dirs():
        p = os.path.join(root, "auth.machine-id")
        try:
            v = open(p, encoding="utf-8").read().strip()
            if v:
                dev["machineId"] = v
                break
        except OSError:
            continue
    for e in candidate_umid_exes():
        mf = os.path.join(os.path.dirname(os.path.dirname(e)), "build-manifest.json")
        try:
            with open(mf, encoding="utf-8") as fh:
                dev["version"] = str(json.load(fh).get("productVersion") or "")
                break
        except Exception:
            continue
    return dev, exe


# ── 取 token ─────────────────────────────────────────────
def _read_store(root):
    raw_path = os.path.join(root, "auth.v1.dat")
    state_path = os.path.join(root, "Local State")
    if not (os.path.isfile(raw_path) and os.path.isfile(state_path)):
        return None
    with open(raw_path, "rb") as fh:
        raw = fh.read()
    if raw[:3] != b"v10":
        return None
    with open(state_path, "r", encoding="utf-8") as fh:
        state = json.load(fh)
    key_blob = base64.b64decode(state["os_crypt"]["encrypted_key"])
    sess = json.loads(_decrypt_gcm(_dpapi_unprotect(key_blob[5:]), raw[3:15], raw[15:]).decode("utf-8"))
    if not sess.get("token"):
        return None
    return sess


def get_token():
    for root in appdata_dirs():
        try:
            sess = _read_store(root)
            if sess:
                out = {"accessToken": sess.get("token") or "",
                       "refreshToken": sess.get("refreshToken") or ""}
                if sess.get("expiresAt"):
                    out["expiresAt"] = sess["expiresAt"]
                return out
        except Exception:
            continue
    return None


# ── 自动写回 ─────────────────────────────────────────────
def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read().lstrip("\ufeff").strip()
        return json.loads(text) if text else default
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return default


def atomic_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _jwt_uid(token):
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return ""
    seg = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return ""
    for k in ("sub", "uid", "user_id", "userId", "id"):
        if data.get(k):
            return str(data[k])
    return ""


def write_device(dev):
    doc = read_json(CONFIG_FILE, {})
    if not isinstance(doc, dict):
        doc = {}
    doc["device"] = dev
    atomic_write_json(CONFIG_FILE, doc)


def write_token(tok):
    uid = _jwt_uid(tok.get("accessToken") or "")
    doc = read_json(ACCOUNTS_FILE, None)
    accounts = doc.get("accounts") if isinstance(doc, dict) else (doc if isinstance(doc, list) else [])
    if not isinstance(accounts, list):
        accounts = []

    def fill(a):
        a["accessToken"] = tok["accessToken"]
        a["refreshToken"] = tok["refreshToken"]
        if uid:
            a["uid"] = uid
        a.setdefault("name", "我的账号")

    hit = False
    for a in accounts:
        if not isinstance(a, dict):
            continue
        if (uid and str(a.get("uid")) == uid) or \
           (tok.get("refreshToken") and a.get("refreshToken") == tok["refreshToken"]):
            fill(a)
            hit = True
            break
    if not hit:
        for a in accounts:  # 优先填充第一个空白账号，而不是每跑一次就新增
            if isinstance(a, dict) and not a.get("accessToken") and not a.get("refreshToken") and not a.get("uid"):
                fill(a)
                hit = True
                break
    if not hit:
        accounts.append({"name": "账号%d" % (len(accounts) + 1), "uid": uid,
                         "accessToken": tok["accessToken"], "refreshToken": tok["refreshToken"]})
    atomic_write_json(ACCOUNTS_FILE, {"accounts": accounts})


def main(argv):
    args = [a.lower() for a in argv]
    as_json = "--json" in args
    no_write = "--no-write" in args or "--dry-run" in args
    only_device = "--device" in args and "--token" not in args
    only_token = "--token" in args and "--device" not in args
    want_device = not only_token
    want_token = not only_device

    dev, exe = (get_device() if want_device else ({}, None))
    tok = (get_token() if want_token else None)

    if as_json:
        out = {}
        if want_device:
            out["device"] = dev
        if want_token:
            out["token"] = tok
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print("=" * 56)
    print("  ① Qoder 一键提取（设备标识 + token，自动写回）")
    print("=" * 56)

    if want_device:
        print("\n── 设备标识（写入 config.json 的 \"device\" 块）──")
        print(json.dumps({"device": dev}, ensure_ascii=False, indent=2))
        if not dev.get("machineToken"):
            print("⚠️ machineToken 为空，跳过写回。未找到 runtime-info.exe？可用 QODER_UMID_EXE 指定其路径后重跑。")
        elif no_write:
            print("ℹ️ 预览模式，未写回 config.json。")
        else:
            write_device(dev)
            print("✅ 已写入 config.json")

    if want_token:
        print("\n── token（写入 accounts.json 的 accounts 数组）──")
        if tok:
            entry = {"name": "我的账号", "uid": _jwt_uid(tok["accessToken"]),
                     "accessToken": tok["accessToken"], "refreshToken": tok["refreshToken"]}
            print(json.dumps(entry, ensure_ascii=False, indent=2))
            if no_write:
                print("ℹ️ 预览模式，未写回 accounts.json。")
            else:
                write_token(tok)
                print("✅ 已写入 accounts.json（按 uid/refreshToken 去重）")
        else:
            print("❌ 未读到有效会话。确认客户端已登录，且本脚本用「同一 Windows 用户」运行；")
            print("   或手动指定 QODER_AUTH_DIR 指向含 auth.v1.dat 的目录。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))