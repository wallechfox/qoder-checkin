// ============================================================================
//  Qoder 每日自动签到 · 金山文档 AirScript 版（修复版）
//  与 Python 版 qoder_core.py 对齐：优先用 accessToken，查活动 401 时才用
//  refreshToken 续期，不再一上来就强制刷新。
// ============================================================================
//
// 【表格列布局】（第 1 行是表头，账号从第 2 行开始，每行一个账号）
//   A  账号 JSON       一行一个账号，格式见下方示例
//   B  是否签到        填「是」才签，其它/空 跳过
//   C  Server酱 token  填了就推，留空不推
//   D  PushPlus token  填了就推，留空不推
//   E  企业微信 key    填了就推，留空不推（群机器人 webhook 的 key，或完整地址都行）
//   F  签到结果        脚本自动写回，平时不用管
//
// 【A 列账号 JSON 怎么写】——把 qoder_gettoken.py 打印的整行直接粘进来即可：
//   {"name":"账号1","refreshToken":"drt-xxx","accessToken":"dt-xxx",
//    "device":{"clientType":"10","machineOS":"x86_64_windows","machineHostname":"DESKTOP-xxx",
//              "machineId":"xxx","machineToken":"xxx","machineCode":"xxx","machineType":"xxx","version":"0.3.4"}}
//
//   · device 里 8 个字段必须齐（来自装了 Qoder 客户端的机器），尤其 machineToken 不能为空
//   · accessToken 优先直接用；查活动被拒(401)时才用 refreshToken 换新的并写回 A 列
//
// 【怎么运行】
//   金山文档 → 效率 → 高级开发 → AirScript 脚本编辑器，把本文件整个粘贴进去，
//   再建一个「定时任务」每天跑一次（建议避开整点）。账号行数会自动识别，不用改。

var date = new Date().getFullYear() + "-" + (new Date().getMonth() + 1).toString().padStart(2, '0') + "-" + new Date().getDate().toString().padStart(2, '0');

// ── Qoder 接口常量（一般不用改）─────────────────────────
var BASE = "https://openapi.qoder.com.cn";   // 国际版改成 https://openapi.qoder.sh
var UA = "Qoder/claim";
var EP_CAMPAIGNS = "/sash/api/v1/me/campaigns";
var EP_CLAIM = "/sash/api/v1/me/campaigns/"; // 后面拼 campaignId + "/claim"
var EP_REFRESH = "/api/v1/deviceToken/refresh";

// ── 列号变量 ───────────────────────────────────────────
var tokenColumn = "A";
var qiandaoColumn = "B";
var serverchanColumn = "C";
var pushplusColumn = "D";
var qywxColumn = "E";
var signinresult = "F";

function nowBJ() {
    var t = new Date();
    return t.getFullYear() + "-" + (t.getMonth() + 1).toString().padStart(2, '0') + "-" + t.getDate().toString().padStart(2, '0')
        + " " + t.getHours().toString().padStart(2, '0') + ":" + t.getMinutes().toString().padStart(2, '0') + ":" + t.getSeconds().toString().padStart(2, '0');
}

// device 对象 → Cosy-* 请求头
function deviceHeaders(device) {
    var map = {
        "clientType": "Cosy-ClientType",
        "machineOS": "Cosy-MachineOS",
        "machineHostname": "Cosy-MachineHostname",
        "machineId": "Cosy-MachineId",
        "machineToken": "Cosy-MachineToken",
        "machineCode": "Cosy-MachineCode",
        "machineType": "Cosy-MachineType",
        "version": "Cosy-Version"
    };
    var h = {};
    for (var k in map) {
        var v = (device && device[k] != null && device[k] !== "") ? String(device[k]) : "";
        if (v !== "") h[map[k]] = v;
    }
    if (!h["Cosy-ClientType"]) h["Cosy-ClientType"] = "10";
    return h;
}

function qHeaders(device, token) {
    var h = { "Accept": "application/json", "User-Agent": UA, "Content-Type": "application/json" };
    var d = deviceHeaders(device);
    for (var k in d) h[k] = d[k];
    if (token) h["Authorization"] = "Bearer " + token;
    return h;
}

// 兼容 WPS AirScript 不同版本的响应状态字段
function respStatus(resp) {
    if (!resp) return 0;
    if (resp.statusCode != null) return resp.statusCode;
    if (resp.status != null) return resp.status;
    return 0;
}

// 用 refreshToken 换 accessToken，返回 {status, accessToken, refreshToken} 或 {error}
function refreshAccess(device, refresh_token) {
    var resp, data;
    try {
        resp = HTTP.post(BASE + EP_REFRESH,
            JSON.stringify({ "refresh_token": refresh_token }),
            { headers: qHeaders(device, ""), timeout: 30000 });
        data = resp.json();
        if (typeof data === "string") { try { data = JSON.parse(data); } catch (e) { data = null; } }
    } catch (e) {
        return { error: "刷新请求失败：" + e.message };
    }
    var tok = (data && (data.token || data.accessToken || data.access_token)) || "";
    if (!tok) return { error: "刷新失败：服务端未返回 token（状态码 " + respStatus(resp) + "）" };
    return {
        status: respStatus(resp),
        accessToken: tok,
        refreshToken: (data && (data.refreshToken || data.refresh_token)) || refresh_token
    };
}

// 查活动列表，返回 {status, data}
function getCampaigns(device, token) {
    var resp = HTTP.get(BASE + EP_CAMPAIGNS, { headers: qHeaders(device, token), timeout: 30000 });
    var data = null;
    try { data = resp.json(); } catch (e) { data = null; }
    if (typeof data === "string") { try { data = JSON.parse(data); } catch (e) { data = null; } }
    return { status: respStatus(resp), data: data };
}

// 领取某个活动，返回 {status, data}
function doClaim(device, token, cid) {
    var resp = HTTP.post(BASE + EP_CLAIM + cid + "/claim",
        JSON.stringify({}),
        { headers: qHeaders(device, token), timeout: 30000 });
    var data = null;
    try { data = resp.json(); } catch (e) { data = null; }
    if (typeof data === "string") { try { data = JSON.parse(data); } catch (e) { data = null; } }
    return { status: respStatus(resp), data: data };
}

// 单账号推送：用该行自己的 C/D/E 通道
function sendNotify(sc, pp, wx, text) {
    var title = "🤖 Qoder 每日签到";
    if (sc !== "") {
        HTTP.post("https://sctapi.ftqq.com/" + sc + ".send",
            { title: title, desp: text },
            { headers: { "Content-Type": "application/json" }, timeout: 60000 });
    }
    if (pp !== "") {
        HTTP.post("https://www.pushplus.plus/send",
            { token: pp, title: title, content: text.replace(/\n/g, "<br>"), channel: "wechat", template: "html" },
            { headers: { "Content-Type": "application/json" }, timeout: 60000 });
    }
    if (wx !== "") {
        var key = wx.split("key=").pop();
        HTTP.post("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + key,
            JSON.stringify({ "msgtype": "text", "text": { "content": text } }),
            { headers: { "Content-Type": "application/json" }, timeout: 60000 });
    }
}

function fmtInt(v) {
    var n = parseInt(Number(v), 10);
    if (isNaN(n)) return String(v);
    return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function countAccounts() {
    var r = 2;
    while (r <= 500) {
        var t = (Application.Range("A" + r).Text || "").trim();
        if (t === "" || t.charAt(0) !== "{") break;
        r++;
    }
    return r - 1;
}

function writeResult(row, statusText) {
    Application.Range(signinresult + row).Value2 = statusText;
}

function notifyOne(sc, pp, wx, icon, name, uid, status, detail) {
    var oneText = "🤖 Qoder 每日签到\n🕒 北京时间：" + nowBJ() + "\n" + icon + " " + name + "(" + uid + ")：" + status + " | " + detail;
    sendNotify(sc, pp, wx, oneText);
    console.log(name + " 已推送通知");
}

// ── 主循环 ─────────────────────────────────────────────
var lastRow = countAccounts();
console.log("共识别到 " + (lastRow - 1) + " 个账号（从第 2 行起）");
if (lastRow < 2) {
    console.log("没有在 A 列第 2 行起读到账号 JSON，请确认 A 列已填入账号、且不在「使用说明」区域内");
}

for (let row = 2; row <= lastRow; row++) {
    var acc = {}, refresh_token = "", access_token = "";
    var accRaw = Application.Range(tokenColumn + row).Text;
    try {
        acc = JSON.parse(accRaw) || {};
        refresh_token = acc.refreshToken || acc.refresh_token || "";
        access_token = acc.accessToken || acc.access_token || "";
    } catch (e) {
        refresh_token = accRaw;   // 不是 JSON，就当纯 refreshToken
    }
    var qiandao = Application.Range(qiandaoColumn + row).Text;
    var servertoken = Application.Range(serverchanColumn + row).Text;
    var pushtoken = Application.Range(pushplusColumn + row).Text;
    var qywxtoken = Application.Range(qywxColumn + row).Text;

    var name = acc.name || ("账号" + (row - 1));
    var uid = acc.uid || "-";
    var device = acc.device || {};

    if (qiandao !== "是") {
        console.log(name + " 签到开关未打开，跳过");
        continue;
    }

    console.log(name + " device=" + JSON.stringify(device));

    // 设备标识校验：machineToken 最关键，缺了基本拿不到活动
    if (!device || !device.machineToken) {
        notifyOne(servertoken, pushtoken, qywxtoken, "⚠️", name, uid, "设备标识缺失", "A 列 JSON 里 device 缺 machineToken，请用 qoder_gettoken.py 重新取整行 JSON 填回");
        writeResult(row, date + " 设备标识缺失");
        continue;
    }

    if (!refresh_token && !access_token) {
        notifyOne(servertoken, pushtoken, qywxtoken, "🔑", name, uid, "需重新登录", "没有 refreshToken/accessToken，请用 qoder_gettoken.py 提取后填回 A 列");
        writeResult(row, date + " 需重新登录");
        continue;
    }

    // 1) 优先直接用 accessToken；A 列没有才用 refreshToken 换
    if (!access_token && refresh_token) {
        var r0 = refreshAccess(device, refresh_token);
        if (r0.error) {
            notifyOne(servertoken, pushtoken, qywxtoken, "🔑", name, uid, "需重新登录", r0.error + "，请重新用 qoder_gettoken.py 提取");
            writeResult(row, date + " 需重新登录");
            continue;
        }
        access_token = r0.accessToken;
        acc.accessToken = r0.accessToken;
        if (r0.refreshToken) acc.refreshToken = r0.refreshToken;
        delete acc.refresh_token;
        delete acc.access_token;
        Application.Range(tokenColumn + row).Value2 = JSON.stringify(acc);
        console.log(name + " 无 accessToken，已用 refreshToken 换新并写回");
    }

    // 2) 查活动
    var q = getCampaigns(device, access_token);
    console.log(name + " 活动状态码=" + q.status);

    // token 无效(401)：用 refreshToken 续期后重试一次
    if (q.status === 401 && refresh_token) {
        var r1 = refreshAccess(device, refresh_token);
        if (!r1.error) {
            access_token = r1.accessToken;
            acc.accessToken = r1.accessToken;
            if (r1.refreshToken) acc.refreshToken = r1.refreshToken;
            delete acc.refresh_token;
            delete acc.access_token;
            Application.Range(tokenColumn + row).Value2 = JSON.stringify(acc);
            console.log(name + " 401 后已刷新 token 并写回，重试查活动");
            q = getCampaigns(device, access_token);
            console.log(name + " 重试活动状态码=" + q.status);
        }
    }

    if (q.status === 401 || (q.status === 0 && !q.data)) {
        notifyOne(servertoken, pushtoken, qywxtoken, "🔑", name, uid, "需重新登录", "token 无效且刷新失败，请用 qoder_gettoken.py 重新提取填回 A 列");
        writeResult(row, date + " 需重新登录");
        continue;
    }

    var data = q.data || {};
    var campaigns = (data && data.campaigns) || [];
    if (!Array.isArray(campaigns)) campaigns = [];

    // 过滤可领取的活动
    var benefits = [], claimable = [];
    for (var i = 0; i < campaigns.length; i++) {
        var c = campaigns[i] || {};
        if (c.actionType === "CLAIM_BENEFIT") {
            benefits.push(c);
            if (c.claimStatus === "CLAIMABLE") claimable.push(c);
        }
    }

    if (claimable.length > 0) {
        var got = 0, fail = 0;
        for (var j = 0; j < claimable.length; j++) {
            var cc = claimable[j];
            var cid = String(cc.campaignId || cc.id || "");
            if (!cid) { fail++; continue; }
            try {
                var cr = doClaim(device, access_token, cid);
                var dd = (cr.data && typeof cr.data === "object") ? cr.data : {};
                if (cr.status === 200 && dd.status === "CLAIMED") {
                    var amt = (dd.benefit && dd.benefit.amount) || (cc.benefit && cc.benefit.amount) || 0;
                    got += parseFloat(amt) || 0;
                } else {
                    fail++;
                }
            } catch (e) {
                fail++;
            }
        }
        if (fail > 0) {
            notifyOne(servertoken, pushtoken, qywxtoken, "❌", name, uid, "失败", fail + "/" + claimable.length + " 个活动领取失败");
            writeResult(row, date + " 失败");
        } else {
            notifyOne(servertoken, pushtoken, qywxtoken, "✅", name, uid, "签到成功", "签到成功 +" + fmtInt(got) + " Credits");
            writeResult(row, date + " 签到成功 +" + fmtInt(got));
        }
        continue;
    }

    // 无可领：判断「今日已领」还是「待下发」
    var now = Date.now() / 1000;
    var hasClaimed = false;
    for (var m = 0; m < benefits.length; m++) {
        var b = benefits[m];
        if (b.claimStatus === "CLAIMED" && (b.startAt || 0) <= now && now < (b.endAt || 0)) {
            hasClaimed = true;
            break;
        }
    }
    if (hasClaimed) {
        notifyOne(servertoken, pushtoken, qywxtoken, "☑️", name, uid, "已领取", "今日已领取");
        writeResult(row, date + " 已领取");
    } else if (benefits.length === 0) {
        notifyOne(servertoken, pushtoken, qywxtoken, "☑️", name, uid, "活动未开放", "活动列表为空（活动结束或未开放）");
        writeResult(row, date + " 活动未开放");
    } else {
        notifyOne(servertoken, pushtoken, qywxtoken, "⏳", name, uid, "待下发", "活动未下发，等下轮");
        writeResult(row, date + " 待下发");
    }
}

console.log("全部处理完成");