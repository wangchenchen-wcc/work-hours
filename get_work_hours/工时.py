# -*- coding: utf-8 -*-
"""每日工时记录与检查脚本

运行后循环显示操作菜单，执行完一个操作后回到菜单，输入 q 退出。可选操作：
  1. 记录打卡时间：输入要记录的日期（直接回车为今天）和签到/签退时间，自动扣除休息时段计算工时；
     同一天重复录入会覆盖旧记录，新日期则追加写入
  2. 检查工时是否满足：从「工时记录.csv」读取本月数据并展示达标情况（只查看，不修改文件）

工时达标规则（不区分月底/月中）：
  - 记录了几天就按几天算：应出勤总工时 = 实际出勤天数 × 8 小时；
  - 差值 = 总工时 - 应出勤总工时，随每天的记录累积（今天超出2h、明天超出1h → 结算时共超出3h）。

文件按月分区存储，每个月份块的第一行是标题行（如「2026年9月：」），
新月份首次记录时自动另起一块从头累计，上月记录原样封存。

所有记录保存在脚本同目录的「工时记录.csv」中，格式：
日期,总工时,本月应出勤天数,实际出勤天数,今日工时,是否满足条件,超出工时/欠工时
休息时段不计工时：12:00~13:30、17:30~18:00。
"""

import calendar
import csv
import os
import sys
from datetime import date, datetime

if getattr(sys, "frozen", False):
    # 打包成 exe 后不存在源码目录，数据目录固定为工时系统根目录（可用环境变量 GONGSHI_HOME 覆盖）
    BASE_DIR = os.environ.get("GONGSHI_HOME") or r"D:\学习规划\脚本\工时系统"
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 工时系统 根目录
RECORD_FILE = os.path.join(BASE_DIR, "工时记录.csv")

# 不计入工时的休息时段（用当天的分钟数表示）
BREAKS = [
    (12 * 60, 13 * 60 + 30),   # 午休 12:00 ~ 13:30
    (17 * 60 + 30, 18 * 60),   # 下午休息 17:30 ~ 18:00
]

HOURS_PER_DAY = 8

FIELDS = ["日期", "总工时", "本月应出勤天数", "实际出勤天数", "今日工时", "是否满足条件", "超出工时/欠工时"]


def parse_time(text):
    """解析时间输入，支持 9:00 / 09:00 / 0900 等格式，返回当天的分钟数"""
    text = text.strip().replace("：", ":")
    for fmt in ("%H:%M", "%H%M"):
        try:
            t = datetime.strptime(text, fmt).time()
            return t.hour * 60 + t.minute
        except ValueError:
            continue
    raise ValueError(f"无法识别的时间「{text}」，请输入如 09:00 的格式")


def parse_date(text, today):
    """解析日期输入，直接回车表示今天；支持 2026-09-22 / 2026/9/22 / 9-22 / 0922 / 20260922 等格式"""
    text = text.strip()
    if not text:
        return today
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%m-%d", "%m/%d", "%m%d"):
        try:
            d = datetime.strptime(text, fmt).date()
            if fmt.startswith("%m"):   # 未带年份的输入默认当前年
                d = d.replace(year=today.year)
            return d
        except ValueError:
            continue
    raise ValueError(f"无法识别的日期「{text}」，请输入如 2026-09-22 的格式，或直接回车表示今天")


def fmt_minutes(minutes):
    h, m = divmod(int(minutes), 60)
    return f"{h}小时{m:02d}分" if m else f"{h}小时"


def calc_work_minutes(start, end):
    """计算 start~end 之间的有效工时（分钟），自动扣除与休息时段重叠的部分"""
    if end <= start:
        raise ValueError("签退时间必须晚于签到时间")
    total = end - start
    for b_start, b_end in BREAKS:
        overlap = min(end, b_end) - max(start, b_start)
        if overlap > 0:
            total -= overlap
    return total


def workdays_in_month(year, month):
    """按日历统计某月的工作日（周一~周五）天数"""
    days = calendar.monthrange(year, month)[1]
    return sum(1 for d in range(1, days + 1) if date(year, month, d).weekday() < 5)


def load_rows():
    """读取全部数据行（自动跳过「2026年9月：」这类月份标题行）并统一成新格式"""
    if not os.path.exists(RECORD_FILE):
        return []
    rows = []
    with open(RECORD_FILE, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d = (r.get("日期") or "").strip()
            if not d:
                continue
            try:
                datetime.strptime(d, "%Y-%m-%d")   # 非日期行（月份标题等）直接跳过
            except ValueError:
                continue
            hours = r.get("今日工时") or r.get("工时(小时)") or "0"
            try:
                hours = float(hours)
            except ValueError:
                hours = 0.0
            row = {k: (r.get(k) or "").strip() for k in FIELDS}
            row["日期"] = d
            row["今日工时"] = f"{hours:.2f}"
            rows.append(row)
    return rows


def build_stats(rows, date_str, day_hours=None, until=None):
    """统计 date_str 所在月份的数据；day_hours 不为 None 时用它替换该日期已有的记录；
    until 不为 None 时只统计该日期（含）之前的记录——用于生成"截至当天"的判定快照"""
    prefix = date_str[:7] + "-"
    days = {}
    for r in rows:
        d = r["日期"]
        if d.startswith(prefix) and (until is None or d <= until):
            days[d] = float(r["今日工时"])
    if day_hours is not None:
        days[date_str] = day_hours
    total = sum(days.values())
    attendance = sum(1 for h in days.values() if h > 0)
    return days, total, attendance


def judge(total, attendance):
    """统一判定标准：记录了几天就按几天算，应出勤总工时 = 实际出勤天数 × 8 小时。
    返回 (应出勤总工时, 总工时-应出勤总工时)，差值随每天记录自然累积"""
    required = attendance * HOURS_PER_DAY
    return required, total - required


def month_title(date_str):
    """月份标题行文本，如 2026-09-30 → 「2026年9月：」"""
    y, m = date_str.split("-")[:2]
    return f"{int(y)}年{int(m)}月："


def _write_rows(rows):
    """把数据行按日期排序写回文件，每个月份块前插入标题行（如「2026年9月：」）"""
    rows.sort(key=lambda r: r["日期"])
    with open(RECORD_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        last_month = None
        for r in rows:
            month = r["日期"][:7]
            if month != last_month:   # 新月份块：先写标题行，实现按月分区
                writer.writerow({"日期": month_title(r["日期"])})
                last_month = month
            writer.writerow(r)


def ensure_migrated():
    """旧格式文件（无月份标题行）一次性迁移：
    按新规则重算每行的「截至当天」累计快照，并补上月份标题行"""
    if not os.path.exists(RECORD_FILE):
        return
    with open(RECORD_FILE, encoding="utf-8-sig") as f:
        if any(line.rstrip("\ufeff\r\n").endswith("：") for line in f):
            return   # 已是新格式
    rows = load_rows()
    total, attendance = 0.0, 0
    for r in rows:   # 按日期排序后逐日累计，重算快照
        hours = float(r["今日工时"])
        if hours > 0:
            attendance += 1
        total += hours
        _, diff = judge(total, attendance)
        r["总工时"] = f"{total:.2f}"
        r["实际出勤天数"] = str(attendance)
        r["是否满足条件"] = "是" if diff >= 0 else "否"
        r["超出工时/欠工时"] = f"超出{diff:.2f}" if diff >= 0 else f"欠{-diff:.2f}"
    _write_rows(rows)
    print("已迁移为按月分区格式，各行判定已按「记录天数 × 8h」重算")


def upsert_row(date_str, total, required_days, attendance, day_hours, diff):
    """写入/更新指定日期的记录（同一天覆盖，新日期追加；月份标题行自动维护）"""
    rows = load_rows()
    new_row = {
        "日期": date_str,
        "总工时": f"{total:.2f}",
        "本月应出勤天数": str(required_days),
        "实际出勤天数": str(attendance),
        "今日工时": f"{day_hours:.2f}",
        "是否满足条件": "是" if diff >= 0 else "否",
        "超出工时/欠工时": f"超出{diff:.2f}" if diff >= 0 else f"欠{-diff:.2f}",
    }
    for i, r in enumerate(rows):
        if r["日期"] == date_str:
            rows[i] = new_row
            break
    else:
        rows.append(new_row)
    _write_rows(rows)


def print_status(total, attendance, required, diff, month_label="本月"):
    print(f"{month_label}总工时：{total:.2f} 小时，实际出勤：{attendance} 天")
    print(f"按「记录天数 × {HOURS_PER_DAY} 小时」标准：应达到 {required:.2f} 小时")
    if diff >= 0:
        print(f"工时满足条件，超出 {diff:.2f} 小时")
    else:
        print(f"工时不满足条件，还欠 {-diff:.2f} 小时")


def input_work_time():
    """交互输入签到/签退时间，返回今日有效工时（分钟）"""
    print("（休息时段不计工时：12:00~13:30、17:30~18:00）")
    while True:
        try:
            start = parse_time(input("请输入签到时间（如 09:00）："))
            break
        except ValueError as e:
            print(e)
    while True:
        try:
            end = parse_time(input("请输入签退时间（如 18:30）："))
            if end <= start:
                print("签退时间必须晚于签到时间，请重新输入")
                continue
            break
        except ValueError as e:
            print(e)
    return calc_work_minutes(start, end)


def record_checkin():
    today = date.today()
    while True:
        try:
            target = parse_date(input("请输入要记录的日期（如 2026-09-22，直接回车为今天）："), today)
            if target > today:
                print("不能记录未来的日期，请重新输入")
                continue
            break
        except ValueError as e:
            print(e)
    target_str = target.strftime("%Y-%m-%d")
    minutes = input_work_time()
    day_hours = minutes / 60

    rows = load_rows()
    # 只统计截至目标日期的记录 → 该行写入的是"截至当天"的累计快照，日后不被新记录刷新
    _, total, attendance = build_stats(rows, target_str, day_hours, until=target_str)
    required_days = workdays_in_month(target.year, target.month)
    required, diff = judge(total, attendance)
    upsert_row(target_str, total, required_days, attendance, day_hours, diff)

    month_label = "本月" if (target.year, target.month) == (today.year, today.month) else f"{target.year}年{target.month}月"
    print(f"{target_str} 有效工时：{fmt_minutes(minutes)}（{day_hours:.2f} 小时）")
    print_status(total, attendance, required, diff, month_label)
    print(f"已保存到：{RECORD_FILE}")


def check_hours():
    """检查工时：只读展示当月每日明细与当前累计达标情况，不修改文件"""
    today = date.today()
    while True:
        try:
            target = parse_date(input("请输入要检查的月份日期（如 2026-09-21，直接回车为今天）："), today)
            break
        except ValueError as e:
            print(e)
    target_str = target.strftime("%Y-%m-%d")
    rows = load_rows()
    days, total, attendance = build_stats(rows, target_str)

    if total == 0 and attendance == 0:
        print(f"{target.year}年{target.month}月还没有打卡记录，无法检查工时")
        return

    required, diff = judge(total, attendance)
    month_label = "本月" if (target.year, target.month) == (today.year, today.month) else f"{target.year}年{target.month}月"
    print(f"{month_label}每日工时：")
    for d in sorted(days):
        print(f"  {d}  {days[d]:.2f} 小时")
    print_status(total, attendance, required, diff, month_label)


def main():
    print("===== 工时记录 =====")
    print("（输入 q 退出）")
    ensure_migrated()   # 旧格式文件自动迁移为按月分区
    while True:
        print()
        print("1. 记录打卡时间")
        print("2. 检查工时是否满足")
        choice = input("请选择操作（1/2/q）：").strip().lower()
        if choice == "q":
            print("已退出，再见！")
            break
        elif choice == "1":
            record_checkin()
        elif choice == "2":
            check_hours()
        else:
            print("无效选择，请输入 1、2 或 q")


if __name__ == "__main__":
    main()
