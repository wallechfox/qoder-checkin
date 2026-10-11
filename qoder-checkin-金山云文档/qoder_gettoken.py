#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qoder_gettoken.py · Qoder 取账号信息（设备标识 + token，单文件零依赖）
════════════════════════════════════════════════════════════
在「装了并登录 Qoder 客户端」的 Windows 机器上跑一次，取到两样东西：

  · token：accessToken + refreshToken（DPAPI 解密密钥 + AES-GCM 解密 auth.v1.dat）
  · 设备标识：Cosy-* 那 8 个字段（客户端 runtime-info.exe + auth.machine-id）

然后合并成「一行 JSON」打印出来，并追加写入同目录的 tokens.txt。
这一行 JSON 就是金山文档表格 A 列要填的内容（直接整行粘过去）。

用法：
    python qoder_gettoken.py                # 取号并打印 + 写入 tokens.txt
    python qoder_gettoken.py --name 我的账号  # 指定账号显示名（默认「我的账号」）

输出示例（正是 A 列要的那一行）：
    {"name":"我的账号","refreshToken":"drt-xxx","accessToken":"dt-xxx",
     "device":{"clientType":"10","machineOS":"x86_64_windows","machineHostname":"DESKTOP-xxx",
               "machineId":"xxx","machineToken":"xxx","machineCode":"xxx","machineType":"xxx","version":"0.3.4"}}

环境变量（可选）：
    QODER_AUTH_DIR   手动指定客户端数据目录（auth.v1.dat / auth.machine-id 所在）
    QODER_UMID_EXE   手动指定 runtime-info.exe 全路径（找不到时用）

前提：
  1. 必须先在本机登录过 Qoder 客户端；
  2. 用「登录客户端时的同一 Windows 用户」运行本脚本（DPAPI 才能解开）。
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
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

UMID_REL = ("resources", "umid", "runtime-info.exe")

HERE = os.path.dirname(os.path.abspath(__file__))


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


def main(argv):
    name = "我的账号"
    for a in argv:
        if a.lower() == "--name" and argv.index(a) + 1 < len(argv):
            name = argv[argv.index(a) + 1] or "我的账号"

    print("=" * 56)
    print("  Qoder 取账号信息（设备标识 + token）")
    print("=" * 56)

    dev, _exe = get_device()
    if not dev.get("machineToken"):
        print("\n⚠️ machineToken 为空：没找到 runtime-info.exe？")
        print("   可用环境变量 QODER_UMID_EXE 指定它的路径后重跑。\n")

    tok = get_token()
    if not tok:
        print("\n❌ 未读到有效会话。请确认：")
        print("   1) 已在本机安装并登录 Qoder 客户端；")
        print("   2) 用「同一 Windows 用户」运行本脚本；")
        print("   3) 或手动指定 QODER_AUTH_DIR 指向含 auth.v1.dat 的目录。")
        return 1

    entry = {
        "name": name,
        "refreshToken": tok.get("refreshToken") or "",
        "accessToken": tok.get("accessToken") or "",
        "device": dev,
    }
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))

    print("\n── 账号信息（整行粘到金山文档表格的 A 列）──────────────────")
    print(line)

    out = os.path.join(HERE, "tokens.txt")
    try:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print("\n✅ 已追加写入 tokens.txt（多账号就每台机器各跑一次，各记一行）")
    except OSError as e:
        print("\n⚠️ 写入 tokens.txt 失败：%s" % e)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))