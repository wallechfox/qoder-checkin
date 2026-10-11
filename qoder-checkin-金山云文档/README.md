# Qoder 每日自动签到（金山云文档版）

基于金山文档 AirScript 的 Qoder 每天领 Credits 自动签到，不用服务器、不用本地常驻，网页 / 手机都能跑。

## 特性

- 多账号，表格逐行管理
- 设备标识（Cosy-* 8 个字段）与 token 一并存在 A 列，脚本自动带齐设备请求头
- 优先直接用 accessToken 签到，失效（401）时自动用 refreshToken 续期并写回表格
- 三种推送：Server酱 / PushPlus / 企业微信，填哪个推哪个，可多填

## 目录结构

```
├── qoder_gettoken.py               # 本地跑一次，提取设备标识 + token
├── Qoder签到-金山文档AirScript.js    # 签到脚本（粘进金山文档）
├── Qoder签到-金山文档.xlsx           # 表格模板
├── tokens.txt                        # 运行后自动生成，存拿到的账号信息（勿提交）
└── LICENSE
```

## 快速开始

### 1. 拿账号信息

在一台装过 Qoder 客户端、并且登录过的 Windows 电脑上：

```bash
python qoder_gettoken.py
```

想给账号起个名，加 `--name`：

```bash
python qoder_gettoken.py --name 我的账号
```

脚本会自动提取 Qoder 客户端本地的 token 和 Cosy-* 设备标识，打印一行完整 JSON，同时追加写入 `tokens.txt`：

```json
{"name":"我的账号","refreshToken":"drt-xxxx","accessToken":"dt-xxxx",
 "device":{"clientType":"10","machineOS":"x86_64_windows","machineHostname":"DESKTOP-xxx",
           "machineId":"xxx","machineToken":"xxx","machineCode":"xxx","machineType":"xxx","version":"0.3.4"}}
```

### 2. 建表格

把 `Qoder签到-金山文档.xlsx` 导入金山文档，或照着下面这个列布局自己建：

| 列 | 内容 | 说明 |
|---|---|---|
| A | 账号 JSON | 上面拿到的那一整行 JSON，一行一账号 |
| B | 是否签到 | 填「是」才签，其它/留空都跳过 |
| C | Server酱 token | 留空不推 |
| D | PushPlus token | 留空不推 |
| E | 企业微信 key | 群机器人 webhook 的 key 或完整地址，留空不推 |
| F | 签到结果 | 自动写回，不用管 |

### 3. 部署 AirScript

金山文档 → 效率 → 高级开发 → AirScript 脚本编辑器：

1. 新建脚本，把 `Qoder签到-金山文档AirScript.js` 整段粘贴进去（账号行数会自动识别，不用改）；
2. 保存，新建一个「定时任务」每天跑一次（避开整点，建议每天 10:00 活动下发之后再跑）。

## 两条前提（重要）

1. 设备标识（尤其 device 里的 `machineToken`）必须来自装了 Qoder 客户端的机器，缺了或过期都会查不到活动；
2. 国际版用户要把脚本开头 `BASE` 从 `https://openapi.qoder.com.cn` 改成 `https://openapi.qoder.sh`。

## 常见问题

- **日志显示「设备标识缺失」**：A 列 JSON 里 device 块缺 `machineToken`。用 `qoder_gettoken.py` 重新提取，把新的一整行 JSON 填回 A 列。
- **一直「待下发」/「活动列表为空」**：多半是设备标识（尤其 `machineToken`）过期，重新提取更新 device 块；也确认下是否当天活动还没下发（Qoder 每日活动不是零点准点发）。
- **提示「需重新登录」**：refreshToken 也失效了。重新跑 `qoder_gettoken.py`，拿新的 JSON 填回 A 列。
- **能跟 Python 版同时跑吗**：不建议。两个程序各拿各的 accessToken，同时跑会互相轮换 token，导致其中一个失效。同一个账号只保留一个签到程序。

## 致谢

签到接口与设备标识提取逻辑参考 [sunp-1/qoder-checkin](https://github.com/sunp-1/qoder-checkin)（MIT）与 qoder-cf-checkin（Cloudflare Worker 版）。

## License

[MIT](./LICENSE)

## 免责声明

本脚本仅用于个人学习与自动化签到，请遵守 Qoder 相关服务条款；它依赖服务端未公开接口，客户端升级可能导致接口失效，使用产生的一切后果由使用者自行承担。