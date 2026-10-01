#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
② 签到 · 02_checkin.py
════════════════════════════════════════════════════════════
在青龙 / NAS 上每天跑一次。读 config.json + accounts.json，
逐个账号：刷新 token（临近过期）→ 查活动 → 有可领就 claim → 推送结果。

依赖 py 文件：qoder_core.py

青龙 cron 示例：
    0 10 * * * python3 /ql/data/scripts/qoder/02_checkin.py

首次部署：先在 Windows 跑 01_extract.py，自动把「设备标识」写进 config.json、
「token」写进 accounts.json，再上传到青龙。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qoder_core as Q  # noqa: E402

if __name__ == "__main__":
    sys.exit(Q.main())