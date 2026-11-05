# -*- coding: utf-8 -*-
"""读取工时页面打卡记录（两种方式）

方式一（推荐）：监听页面自己发出的考勤查询请求（queryCardRecord），
直接拿 JSON 响应，天然就是 clockInDate / earlyClockInTime / lateClockInTime，
与弹窗 DOM 结构无关，最稳定。

方式二（备用）：如果某天数据不在该接口里，再用 DOM 兜底——
弹窗打开后用 page.inner_text() 取文本、page.content() 存 HTML 供分析。
"""

import importlib.util
import json
import os
import sys
from datetime import datetime

from playwright.sync_api import sync_playwright

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    # 打包成 exe 后不存在源码目录，数据目录固定为工时系统根目录（可用环境变量 GONGSHI_HOME 覆盖）
    BASE_DIR = os.environ.get("GONGSHI_HOME") or r"D:\学习规划\脚本\工时系统"
else:
    BASE_DIR = os.path.dirname(SCRIPT_DIR)  # 工时系统 根目录（存放 csv / json / 浏览器数据）
JSON_DIR = os.path.join(BASE_DIR, "json")
records = []  # 收集所有响应里的打卡记录


def _load_core():
    """加载同目录 工时.py，复用其工时计算与 CSV 写入逻辑"""
    spec = importlib.util.spec_from_file_location("gongshi_core", os.path.join(SCRIPT_DIR, "工时.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load_core()


def save_to_csv(rows):
    """把完整记录（日期+签到+签退）写入 工时记录.csv，同日期覆盖

    签到或签退缺失的记录（如当天还没打签退）不写入。
    """
    written = 0
    for d, s, e in rows:
        if not s or not e:
            continue
        try:
            minutes = core.calc_work_minutes(core.parse_time(s), core.parse_time(e))
            target = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        day_hours = minutes / 60
        # 只统计截至当天的记录 → 该行写入的是"截至当天"的累计快照，日后不被新记录刷新
        _, total, attendance = core.build_stats(core.load_rows(), d, day_hours, until=d)
        required_days = core.workdays_in_month(target.year, target.month)
        required, diff = core.judge(total, attendance)
        core.upsert_row(d, total, required_days, attendance, day_hours, diff)
        written += 1
    return written


def handle_response(resp):
    """监听考勤接口响应，把 JSON 记录收进 records"""
    if "queryCardRecord" not in resp.url:
        return
    try:
        data = resp.json()
    except Exception:
        return
    if isinstance(data, list):
        records.extend(data)


def read_popup(page):
    """解析打卡记录弹窗，返回 [(年-月-日, 签到, 签退), ...]

    弹窗里日期只有 月/日（如 09/24），年份按当前年推算；
    因日历只能看当前或过去的月份，弹窗月份大于当前月时视为上一年。
    """
    now = datetime.now()
    rows = []
    seen = set()
    items = page.locator(".recordDialog .record-item")
    for i in range(items.count()):
        it = items.nth(i)
        mmdd = it.locator(".left-part div").first.inner_text().strip()  # 如 09/24
        if "/" not in mmdd:
            continue
        m, d = (int(x) for x in mmdd.split("/"))
        year = now.year - 1 if m > now.month else now.year
        day = f"{year}-{m:02d}-{d:02d}"
        if day in seen:  # 弹窗内重复日期只取第一条
            continue
        seen.add(day)
        start = it.locator(".startTime .time").inner_text().strip()
        end = it.locator(".endTime .time").inner_text().strip()
        rows.append((day, start, end))
    return rows


def run(show=False):
    """启动 Edge 抓取打卡记录并写入 CSV，返回写入 CSV 的完整记录条数

    channel="msedge"：直接使用系统已安装的 Edge，无需下载 Playwright 内核；
    持久化用户目录：登录一次后免登录；默认后台无窗口运行（show=True 显示窗口）。
    """
    written = 0
    with sync_playwright() as p:
        profile = os.path.join(BASE_DIR, ".edge-profile")
        context = p.chromium.launch_persistent_context(profile, channel="msedge", headless=not show)
        page = context.new_page()
        page.on("response", handle_response)  # 关键：挂上响应监听
        page.goto("https://hr.huawei.com/apps/servicetimeflow/#/myServicetime")

        # 已登录则直接过；无登录态时：可见模式等手动登录，后台模式报错提示
        try:
            page.wait_for_selector("#attendance-calendar", timeout=15000)
        except Exception:
            if show:
                print("请在打开的浏览器中完成登录（等待中，最长 5 分钟）…")
                page.wait_for_selector("#attendance-calendar", timeout=300000)
            else:
                context.close()
                raise SystemExit("未检测到登录态：请先运行一次 python read_work_hours_ui.py --show 手动登录")
        page.wait_for_timeout(2000)  # 等日历数据渲染

        # ---- 全自动：点击"打卡记录"链接 → 弹窗列出全部记录 → 解析 ----
        try:
            page.locator("#attendance-calendar .link-box .record").click(timeout=5000)
        except Exception:
            page.get_by_text("打卡记录").first.click()
        page.wait_for_selector(".recordDialog", state="visible", timeout=10000)
        page.wait_for_timeout(800)  # 等弹窗内容渲染

        rows = read_popup(page)
        print("\n---- 打卡记录弹窗 ----")
        for d, s, e in rows:
            print(f"{d}  签到 {s or '--'}  签退 {e or '--'}")
        print(f"共 {len(rows)} 条")
        out2 = os.path.join(JSON_DIR, "popup_records.json")
        with open(out2, "w", encoding="utf-8") as f:
            json.dump([{"date": d, "start": s, "end": e} for d, s, e in rows],
                      f, ensure_ascii=False, indent=2)
        print(f"已保存到：{out2}")

        # 写入 工时记录.csv（工时系统直接从这里读数据）
        written = save_to_csv(rows)
        print(f"已写入 工时记录.csv：{written} 条完整记录（{core.RECORD_FILE}）")

        # 关闭弹窗（便于后续扩展继续操作）
        try:
            page.locator(".recordDialog .aui-dialog-box__close").click(timeout=3000)
        except Exception:
            pass

        context.close()

    # ---- 提取完整记录：日期 + 签到 + 签退 ----
    complete = []
    for r in records:
        d = r.get("clockInDate")
        s = r.get("earlyClockInTime")
        e = r.get("lateClockInTime")
        if d and s and e:
            complete.append((d, s, e))

    for d, s, e in complete:
        print(f"{d}  签到 {s}  签退 {e}")
    print(f"\n共 {len(complete)} 条完整记录")

    # 保存原始数据，便于后续写入 工时记录.csv
    out = os.path.join(JSON_DIR, "ui_records.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"原始响应已保存到：{out}")
    return written


if __name__ == "__main__":
    run(show="--show" in sys.argv)
