# Qoder 每日自动签到

> 纯 Python 标准库、零第三方依赖的 Qoder「每天领 100 Credits」多账号自动签到脚本，面向 **青龙面板 / NAS（Linux）** 部署。

Qoder 没有公开的登录接口，签到所需的 `token` 与 `Cosy-*` 设备标识都只存在于客户端本地。本项目解决两件事：

1. 在 **Windows（装了并登录 Qoder 客户端）** 上一次性提取设备标识 + token，并**自动写回**配置文件；
2. 在 **青龙 / NAS** 上每天定时签到，token 到期自动刷新，结果推送到微信等渠道。

## ✨ 特性

- **零第三方依赖**：只依赖 Python 标准库；`cryptography` 装了更快、没装退回 Windows 自带 CNG（bcrypt）
- **多账号**：`accounts.json` 写几个签几个，按 uid / refreshToken 去重
- **一键提取 + 自动写回**：`01_extract.py` 解密客户端本地凭据并直接落盘，无需手动复制粘贴
- **token 自动刷新**：到期前主动刷新，刷新结果持久化，避免环境变量越用越失效
- **多通道推送**：企业微信群机器人 / PushPlus / 自定义 Webhook
- **幂等安全**：claim 接口幂等，重复运行不会重复发币，无需补签 / 去重 / 冷却

## 🗂 目录结构

```
qoder-checkin/
├── 01_extract.py         # ① Windows：一键提取设备标识 + token，自动写回
├── 02_checkin.py         # ② 青龙/NAS：每天签到入口
├── qoder_core.py         #    共用实现（配置 / 请求 / 刷新 / 签到 / 推送）
├── config.json           #    设备标识（Cosy-*） + 推送 + 端点
├── accounts.example.json #    账号模板（复制为 accounts.json）
├── accounts.json         #    账号（多账号数组，01_extract 自动写回/生成）
└── LICENSE
```

## 🚀 快速开始

### 1. Windows 一键提取（只需一次）

在**装了并登录 Qoder 客户端**的 Windows 机器上运行：

```powershell
python 01_extract.py
```

脚本会自动：

- 把 `Cosy-*` 设备标识写进 `config.json` 的 `device` 块；
- 把 `token` + `refreshToken` 写进 `accounts.json` 的 `accounts` 数组（按 uid 去重，有旧账号就更新、没有就填充空白账号或追加）。

只想预览不落盘：`python 01_extract.py --no-write`

### 2. 配置推送（可选）

编辑 `config.json` 的 `notify`，或直接用环境变量（见下表）。

### 3. 青龙部署

1. 把整个目录上传到青龙，例如 `/ql/data/scripts/qoder/`；
2. 确认 `config.json` 和 `accounts.json` 已就绪（第 1 步已自动写回）；
3. 新建任务，命令填：

```bash
python3 /ql/data/scripts/qoder/02_checkin.py
```

定时表达式（北京时间，每天 10 点）：

```
0 10 * * *
```

> 只需 Python 3.8+，无需 `pip install` 任何东西。

## ⚙️ 配置

### config.json

```jsonc
{
  "device": {                 // 01_extract.py 自动写回，一般不用手填
    "clientType": "10",
    "machineOS": "",
    "machineHostname": "",
    "machineId": "",
    "machineToken": "",
    "machineCode": "",
    "machineType": "",
    "version": ""
  },
  "notify": {                 // 推送，留空即关闭对应渠道
    "qywxToken": "",          // 企业微信群机器人 webhook 里的 key 段
    "plusplusToken": "",      // PushPlus 的 token
    "webhook": ""             // 自定义 Webhook（POST JSON {title, content}）
  },
  "baseUrls": ["https://openapi.qoder.com.cn"],   // 国际服换成 https://openapi.qoder.sh
  "refreshMarginH": 72                            // token 到期前多少小时提前刷新
}
```

### accounts.json

```jsonc
{
  "accounts": [
    { "name": "账号1", "uid": "", "accessToken": "", "refreshToken": "" },
    { "name": "账号2", "uid": "", "accessToken": "", "refreshToken": "" }
  ]
}
```

多账号就多写几个对象；其实 `01_extract.py` 跑完会自动写入，通常不用手改。

## 🔐 环境变量（可选，优先级高于文件）

| 变量 | 说明 |
| --- | --- |
| `QODER_ACCOUNTS` | JSON 账号数组，覆盖 `accounts.json`（env 静态时靠缓存持久化轮换后的 token） |
| `QODER_COSY_*` | 覆盖 `config.json` 的 device，如 `QODER_COSY_MACHINE_TOKEN`、`QODER_COSY_CLIENT_TYPE` |
| `QODER_BASE_URL` | 覆盖 `baseUrls`，国际服填 `https://openapi.qoder.sh` |
| `QODER_CONFIG_FILE` | 指定 `config.json` 路径（`01_extract.py` 写回也用） |
| `QODER_ACCOUNTS_FILE` | 指定 `accounts.json` 路径 |
| `QYWX_TOKEN` | 企业微信群机器人 key |
| `PLUSPLUS_TOKEN` | PushPlus token |
| `QODER_WEBHOOK` | 自定义 Webhook |

## 📊 运行结果

每次运行对每个账号返回一个状态：

| 状态 | 含义 |
| --- | --- |
| `claimed` | 签到成功，到账 N Credits |
| `already` | 今日已领取 / 活动未开放 / 列表为空 |
| `pending` | 活动未下发，下轮再试（正常，Qoder 每日活动不是零点准时发） |
| `login_required` | token 与 refreshToken 均失效，需回 Windows 重跑 `01_extract.py` |
| `error` | 领取或查询失败 |

## ❓ 常见问题

**一直 `pending` / 活动列表突然变空？**
大概率是设备标识（尤其 `Cosy-MachineToken`）过期。回 Windows 重跑 `01_extract.py` 更新 `config.json` 即可。

**请求被代理拦了？**
取消 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量。

**国际版用户？**
把 `config.json` 的 `baseUrls` 改成 `https://openapi.qoder.sh`，或设环境变量 `QODER_BASE_URL`。

**token 快过期怎么办？**
脚本到期前 72 小时会自动刷新并持久化；`refreshToken` 也失效时才需要重新提取。

## 🙏 致谢

解密（DPAPI + AES-GCM）与设备标识（`runtime-info.exe`）提取逻辑参考：

- [sunp-1/qoder-checkin](https://github.com/sunp-1/qoder-checkin)（MIT）
- qoder-cf-checkin（Cloudflare Worker 版）

## 📄 License

[MIT](LICENSE)

## ⚠️ 免责声明

本项目为**第三方非官方工具**，与 Qoder 官方无关，也未获其背书。它依赖服务端**未公开接口**，客户端升级可能导致接口路径 / 字段 / 鉴权方式变化而失效；自动化领取可能不符合服务条款。请仅用于你自己的账号，自行判断并承担风险。