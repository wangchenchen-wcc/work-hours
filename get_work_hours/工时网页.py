# -*- coding: utf-8 -*-
"""工时记录网页版

运行：python 工时网页.py
启动后会自动打开浏览器（默认 http://127.0.0.1:8765，按 Ctrl+C 停止服务）。

复用同目录「工时.py」中的计算与存储逻辑，数据仍保存在「工时记录.csv」，
网页版和控制台版可以混用。
仅使用 Python 标准库，无需安装任何依赖。
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

if getattr(sys, "frozen", False):
    # 打包成 exe 后，随包分发的 工时.py / read_work_hours_ui.py 解压在 _MEIPASS 临时目录
    SCRIPT_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_module(name, filename):
    """加载同目录下的 Python 模块"""
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPT_DIR, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load_module("gongshi_core", "工时.py")

# 法定节假日安排（名称, 开始日, 结束日）。
# 说明：以上为 2026 年安排，国务院办公厅每年 11 月前后公布次年放假安排，
# 届时在此列表末尾补充/修改即可（列表需按日期升序）。
HOLIDAYS = [
    ("元旦", "2026-01-01", "2026-01-03"),
    ("春节", "2026-02-15", "2026-02-21"),
    ("清明节", "2026-04-04", "2026-04-06"),
    ("劳动节", "2026-05-01", "2026-05-05"),
    ("端午节", "2026-06-19", "2026-06-21"),
    ("中秋节", "2026-09-25", "2026-09-27"),
    ("国庆节", "2026-10-01", "2026-10-07"),
    ("元旦", "2027-01-01", "2027-01-03"),
]
PAYDAY = 15  # 每月发薪日


def countdown_info():
    """计算下一个节假日和下次发薪日的倒计时"""
    today = date.today()
    holiday = None
    for name, start_s, end_s in HOLIDAYS:
        start, end = datetime.strptime(start_s, "%Y-%m-%d").date(), datetime.strptime(end_s, "%Y-%m-%d").date()
        if start <= today <= end:
            holiday = {"name": name, "start": start_s, "end": end_s, "days": 0, "ongoing": True}
            break
        if start > today:
            holiday = {"name": name, "start": start_s, "end": end_s, "days": (start - today).days, "ongoing": False}
            break

    if today.day <= PAYDAY:
        pay = today.replace(day=PAYDAY)
    else:  # 本月已过发薪日，取下月
        pay = (today.replace(day=28) + timedelta(days=7)).replace(day=PAYDAY)
    payday = {"date": pay.strftime("%Y-%m-%d"), "days": (pay - today).days}

    return {"ok": True, "today": today.strftime("%Y-%m-%d"), "holiday": holiday, "payday": payday}


HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>工时记录</title>
<style>
  * { box-sizing: border-box; }
  :root {
    --text: #10324a; --muted: #527089; --border: rgba(110,160,195,.45);
    --panel: rgba(255,255,255,.82); --panel-2: rgba(255,255,255,.72);
    --primary: #2f80d8; --good: #15803d; --warn: #b45309;
  }
  body {
    margin: 0; color: var(--text); min-height: 100vh;
    font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
    background: #8fd4ff;
    transition: background-color 1.2s ease;
  }
  body.night {
    --text: #e8f0ff; --muted: #9db3d1; --border: rgba(120,165,220,.38);
    --panel: rgba(11,24,45,.82); --panel-2: rgba(18,35,60,.74);
    --primary: #6aa9ff; --good: #4ade80; --warn: #fbbf24;
    background: #081326;
  }
  .wrap { position: relative; z-index: 2; max-width: 760px; margin: 0 auto; padding: 190px 18px 56px; }

  /* ===== 背景：蓝天白云 + 绿水青山 ===== */
  .scape { position: fixed; inset: 0; z-index: 0; pointer-events: none; overflow: hidden; }
  .scape .sky { position: absolute; inset: 0; transition: opacity 1.2s ease; }
  .scape .sky.day { background: linear-gradient(180deg, #63bdff 0%, #9ad8ff 34%, #d6f0ff 56%, #e8f8ec 100%); }
  .scape .sky.night {
    background: linear-gradient(180deg, #071228 0%, #0d2240 38%, #12324e 62%, #0d2a2c 100%);
    opacity: 0;
  }
  body.night .scape .sky.day { opacity: 0; }
  body.night .scape .sky.night { opacity: 1; }
  .sun-orb, .moon-orb {
    position: fixed; left: 0; top: 0; z-index: 3; pointer-events: none;
    border-radius: 50%; opacity: 0; transform: translate(-50%, -50%);
    transition: opacity .8s ease;
  }
  .sun-orb {
    width: 58px; height: 58px;
    background: radial-gradient(circle at 36% 32%, #fffef4 0%, #ffe97a 46%, #ffc93c 100%);
    box-shadow: 0 0 22px 8px rgba(255,236,150,.75), 0 0 70px 30px rgba(255,214,90,.25);
  }
  .moon-orb {
    width: 50px; height: 50px;
    background: radial-gradient(circle at 38% 34%, #ffffff 0%, #e6ecf9 52%, #c4d1e6 100%);
    box-shadow: 0 0 18px 6px rgba(210,228,255,.55), 0 0 64px 26px rgba(150,190,255,.18);
  }
  .moon-orb::after {
    content: ""; position: absolute; left: 14px; top: 15px; width: 9px; height: 9px;
    border-radius: 50%; background: rgba(158,175,205,.35);
    box-shadow: 15px 10px 0 -1px rgba(158,175,205,.3), 7px 24px 0 -2px rgba(158,175,205,.28);
  }
  .scape .cloud {
    position: absolute; left: 0; width: 180px; height: 92px; opacity: .92;
    filter: drop-shadow(0 10px 18px rgba(96,150,190,.18));
    transition: opacity 1.2s ease, filter 1.2s ease;
    animation: drift var(--dur, 80s) linear infinite;
    animation-delay: var(--delay, 0s);
  }
  .scape .cloud i {
    position: absolute; left: 0; bottom: 0; width: 180px; height: 44px;
    border-radius: 999px; background: #fff;
  }
  .scape .cloud::before, .scape .cloud::after {
    content: ""; position: absolute; background: #fff; border-radius: 50%;
  }
  .scape .cloud::before { width: 64px; height: 64px; left: 24px; bottom: 16px; }
  .scape .cloud::after  { width: 92px; height: 92px; left: 72px; bottom: 8px; }
  @keyframes drift {
    from { transform: translateX(-20vw) scale(var(--s, 1)); }
    to   { transform: translateX(120vw) scale(var(--s, 1)); }
  }
  .scape .land { position: absolute; left: 0; bottom: 0; width: 100%; height: 74vh; min-height: 480px; transition: filter 1.2s ease; }
  body.night .scape .cloud { opacity: .28; filter: drop-shadow(0 10px 18px rgba(0,0,0,.35)); }
  body.night .scape .land { filter: brightness(.5) saturate(.7) hue-rotate(8deg); }
  .scape .river-flow {
    fill: none; stroke: rgba(255,255,255,.55); stroke-width: 7;
    stroke-linecap: round; stroke-dasharray: 28 42;
    animation: flow 3.4s linear infinite;
  }
  .scape .river-flow.flow2 {
    stroke-width: 4; stroke-opacity: .5; stroke-dasharray: 16 52;
    animation-duration: 2.4s;
  }
  @keyframes flow {
    from { stroke-dashoffset: 0; }
    to   { stroke-dashoffset: -140; }
  }

  .hero {
    display: flex; align-items: center; gap: 14px; color: #fff;
    background: linear-gradient(135deg, #2f8ce0 0%, #4ab3ea 55%, #2f9e63 100%);
    border: 1px solid rgba(255,255,255,.55);
    border-radius: 18px; padding: 20px 24px;
    box-shadow: 0 14px 30px -16px rgba(21,86,132,.55), inset 0 1px 0 rgba(255,255,255,.28);
    animation: rise .45s ease both;
  }
  .hero .ico {
    width: 46px; height: 46px; border-radius: 14px; flex: none;
    background: rgba(255,255,255,.18); display: flex; align-items: center; justify-content: center;
  }
  .hero h1 { margin: 0; font-size: 21px; letter-spacing: .5px; }
  .hero p { margin: 3px 0 0; font-size: 12.5px; opacity: .85; }
  .hero .tag { margin-left: auto; flex: none; font-size: 12px; background: rgba(255,255,255,.16); padding: 5px 12px; border-radius: 999px; }
  @media (max-width: 560px) { .hero .tag { display: none; } }

  .tips { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 14px; animation: rise .45s ease both; animation-delay: .04s; }
  @media (max-width: 560px) { .tips { grid-template-columns: 1fr; } }
  .tip {
    display: flex; align-items: center; gap: 12px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 13px 16px;
    box-shadow: 0 1px 2px rgba(23,78,120,.10), 0 14px 30px -22px rgba(23,78,120,.45);
  }
  .tip .tico { width: 38px; height: 38px; border-radius: 11px; flex: none; display: flex; align-items: center; justify-content: center; }
  .tip .tico.h { background: rgba(251,146,60,.14); color: var(--warn); }
  .tip .tico.p { background: rgba(52,211,153,.14); color: var(--good); }
  .tip .tx { min-width: 0; }
  .tip .t1 { font-size: 13.5px; font-weight: 600; }
  .tip .t1 .num { color: #2b6fb5; font-size: 17px; font-weight: 700; padding: 0 2px; }
  .tip .t2 { font-size: 12px; color: var(--muted); margin-top: 2px; }

  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 16px;
    padding: 20px 22px; margin-top: 16px;
    backdrop-filter: blur(12px) saturate(120%); -webkit-backdrop-filter: blur(12px) saturate(120%);
    box-shadow: 0 1px 2px rgba(23,78,120,.10), 0 18px 38px -26px rgba(23,78,120,.55);
    animation: rise .45s ease both;
  }
  .card:nth-of-type(2) { animation-delay: .07s; }
  .card h2 { margin: 0 0 16px; font-size: 15.5px; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .card h2 .t { display: flex; align-items: center; gap: 8px; }
  .card h2 .dot { width: 8px; height: 8px; border-radius: 50%; background: linear-gradient(135deg, #3fa9ea, #2f9e63); box-shadow: 0 0 8px rgba(47,128,216,.55); }
  .card h2 input[type="month"] { width: 158px; padding: 7px 10px; font-size: 13px; }

  form { display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 12px; align-items: end; }
  @media (max-width: 620px) { form { grid-template-columns: 1fr 1fr; } form button { grid-column: span 2; } }
  label { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; color: var(--muted); font-weight: 500; }
  input {
    border: 1px solid rgba(110,160,195,.55); border-radius: 10px; padding: 9px 11px; font-size: 14px;
    background: rgba(255,255,255,.92); color: var(--text); color-scheme: light;
    transition: border-color .15s, box-shadow .15s, background .15s;
    font-family: inherit;
  }
  input:focus { outline: none; border-color: var(--primary); background: #fff; box-shadow: 0 0 0 3px rgba(47,128,216,.20); }
  button {
    border: 0; border-radius: 10px; padding: 10px 26px; font-size: 14px; font-weight: 600;
    color: #fff; cursor: pointer; font-family: inherit;
    background: linear-gradient(135deg, #3b9ae8, #2f9e63);
    box-shadow: 0 6px 16px -6px rgba(47,128,216,.65);
    transition: transform .12s, box-shadow .12s, filter .12s;
  }
  button:hover { filter: brightness(1.1); transform: translateY(-1px); box-shadow: 0 10px 22px -8px rgba(34,120,170,.7); }
  button:active { transform: translateY(0); }

  .msg { display: none; margin-top: 14px; font-size: 13.5px; line-height: 1.7; border-radius: 10px; padding: 11px 14px; }
  .msg.ok { display: block; background: rgba(34,197,94,.14); color: #15803d; border: 1px solid rgba(34,160,80,.35); animation: rise .3s ease both; }
  .msg.err { display: block; background: rgba(245,158,11,.16); color: #b45309; border: 1px solid rgba(217,119,6,.35); animation: rise .3s ease both; }

  .syncinfo { min-height: 16px; margin-bottom: 8px; font-size: 12.5px; color: var(--muted); }
  .syncinfo.ok { color: var(--good); }
  .syncinfo.err { color: var(--warn); }

  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(128px, 1fr)); gap: 10px; margin-bottom: 16px; }
  .stat { background: var(--panel-2); border: 1px solid var(--border); border-radius: 12px; padding: 12px 13px; transition: transform .15s, box-shadow .15s, border-color .15s; }
  .stat:hover { transform: translateY(-2px); border-color: rgba(100,160,200,.6); box-shadow: 0 10px 24px -14px rgba(23,78,120,.6); }
  .stat .k { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); }
  .stat .k svg { flex: none; }
  .stat .v { font-size: 17px; font-weight: 700; margin-top: 6px; font-variant-numeric: tabular-nums; }
  .stat .v small { font-size: 12px; font-weight: 500; color: var(--muted); }
  .v.good { color: var(--good); }
  .v.warn { color: var(--warn); }

  .badge { display: inline-flex; align-items: center; gap: 6px; padding: 3px 11px; border-radius: 999px; font-size: 13px; font-weight: 600; }
  .badge::before { content: ""; width: 7px; height: 7px; border-radius: 50%; }
  .badge.ok { background: rgba(34,197,94,.16); color: var(--good); }
  .badge.ok::before { background: var(--good); box-shadow: 0 0 6px var(--good); }
  .badge.bad { background: rgba(245,158,11,.18); color: var(--warn); }
  .badge.bad::before { background: var(--warn); box-shadow: 0 0 6px var(--warn); }

  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th { text-align: left; padding: 6px 8px; color: var(--muted); font-size: 12px; font-weight: 500; border-bottom: 1px solid var(--border); }
  td { padding: 9px 8px; border-bottom: 1px solid rgba(110,160,195,.28); }
  tbody tr { transition: background .12s; }
  tbody tr:hover { background: rgba(214,238,250,.6); }
  td.date { font-variant-numeric: tabular-nums; color: var(--muted); }
  td.hours { font-weight: 600; font-variant-numeric: tabular-nums; }
  td.hours small { font-size: 11px; font-weight: 500; color: var(--muted); }
  td.hours.good { color: var(--good); }
  .bar { height: 9px; background: #d5e8f4; border-radius: 999px; overflow: hidden; }
  .bar > i {
    display: block; height: 100%; width: 0; border-radius: 999px;
    background: linear-gradient(90deg, #7cc9f5, #2f80d8);
    box-shadow: 0 0 8px rgba(47,128,216,.4);
    transition: width .55s cubic-bezier(.22,1,.36,1);
  }
  .bar > i.over { background: linear-gradient(90deg, #43c97a, #1f9d55); box-shadow: 0 0 8px rgba(34,160,80,.4); }

  .empty { color: #4f7188; text-align: center; padding: 26px 0 18px; font-size: 13.5px; }
  .empty svg { display: block; margin: 0 auto 8px; opacity: .55; }

  footer { text-align: center; color: #4b7088; font-size: 12px; margin-top: 22px; }

  @keyframes rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }

  /* ===== 昼夜主题过渡 ===== */
  .card, .tip, .hero, input, .msg, th, td, footer, .bar {
    transition: background-color 1.2s ease, color 1.2s ease, border-color 1.2s ease;
  }
  .stat { transition: transform .15s, box-shadow 1.2s ease, border-color 1.2s ease, background-color 1.2s ease; }
  .stat .v, td.hours, .syncinfo, .tip .t1 .num, .badge { transition: color 1.2s ease; }
  body.night .hero {
    background: linear-gradient(135deg, #16305a 0%, #1d3f60 55%, #18483f 100%);
    box-shadow: 0 14px 30px -16px rgba(0,0,0,.7), inset 0 1px 0 rgba(255,255,255,.18);
  }
  body.night input { background: rgba(12,26,48,.9); border-color: rgba(120,165,220,.45); color-scheme: dark; }
  body.night input:focus { background: #0e2036; }
  body.night .msg.ok { background: rgba(34,197,94,.16); color: #86efac; border-color: rgba(74,222,128,.4); }
  body.night .msg.err { background: rgba(245,158,11,.18); color: #fcd34d; border-color: rgba(251,191,36,.4); }
  body.night .bar { background: #1d3150; }
  body.night tbody tr:hover { background: rgba(80,120,170,.20); }
  body.night .stat:hover { border-color: rgba(120,175,225,.5); box-shadow: 0 10px 24px -14px rgba(0,0,0,.6); }
  body.night .empty, body.night footer { color: #8fa9c8; }
  body.night .tip .t1 .num { color: #8cc6ff; }
  body.night .card h2 .dot { background: linear-gradient(135deg, #7cc4ff, #43c97a); box-shadow: 0 0 8px rgba(124,196,255,.6); }
</style>
</head>
<body>
<div class="scape" aria-hidden="true">
  <div class="sky day"></div>
  <div class="sky night"></div>
  <span class="cloud" style="top:5%;--s:.85;--dur:86s;--delay:-22s"><i></i></span>
  <span class="cloud" style="top:14%;--s:1.15;--dur:104s;--delay:-64s"><i></i></span>
  <span class="cloud" style="top:24%;--s:.6;--dur:72s;--delay:-8s"><i></i></span>
  <span class="cloud" style="top:9%;--s:.7;--dur:94s;--delay:-48s"><i></i></span>
  <svg class="land" viewBox="0 0 1440 700" preserveAspectRatio="none">
    <defs>
      <linearGradient id="farHill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#a6ddbd"/><stop offset="1" stop-color="#7ec49a"/>
      </linearGradient>
      <linearGradient id="midHill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#6fbf88"/><stop offset="1" stop-color="#4ea76c"/>
      </linearGradient>
      <linearGradient id="grassG" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#5fc47b"/><stop offset="1" stop-color="#3f9c5b"/>
      </linearGradient>
      <linearGradient id="soilG" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#e0c581"/><stop offset="1" stop-color="#bd9a53"/>
      </linearGradient>
      <linearGradient id="riverG" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#c6ecff"/><stop offset="1" stop-color="#57b7ea"/>
      </linearGradient>
      <g id="tree">
        <ellipse cx="0" cy="3" rx="24" ry="7" fill="#2f7d4f" opacity=".18"/>
        <rect x="-4" y="-38" width="8" height="42" rx="3" fill="#7b5734"/>
        <circle cx="0" cy="-58" r="22" fill="#2f8a55"/>
        <circle cx="-16" cy="-45" r="17" fill="#35975c"/>
        <circle cx="16" cy="-45" r="17" fill="#35975c"/>
        <circle cx="-4" cy="-64" r="12" fill="#43a769"/>
      </g>
    </defs>
    <path d="M0,250 C120,190 230,235 350,205 C470,175 560,220 690,192 C820,164 930,218 1060,188 C1190,158 1320,212 1440,180 L1440,700 L0,700 Z" fill="url(#farHill)"/>
    <path d="M0,340 C150,280 290,325 430,300 C570,275 680,335 830,310 C980,285 1110,340 1250,315 C1340,298 1400,325 1440,312 L1440,700 L0,700 Z" fill="url(#midHill)"/>
    <path d="M0,430 C170,385 340,437 520,413 C700,389 860,443 1040,417 C1200,393 1340,433 1440,411 L1440,700 L0,700 Z" fill="url(#grassG)"/>
    <path d="M0,620 C180,596 360,636 560,616 C760,596 940,640 1140,616 C1280,600 1380,622 1440,610 L1440,700 L0,700 Z" fill="url(#soilG)"/>
    <path d="M1174,330 C1230,380 1120,420 1130,474 C1142,532 1066,564 1090,622 C1110,670 1058,696 1078,700"
          fill="none" stroke="#3d7f59" stroke-opacity=".5" stroke-width="64" stroke-linecap="round"/>
    <path d="M1174,330 C1230,380 1120,420 1130,474 C1142,532 1066,564 1090,622 C1110,670 1058,696 1078,700"
          fill="none" stroke="url(#riverG)" stroke-width="46" stroke-linecap="round"/>
    <path d="M1174,330 C1230,380 1120,420 1130,474 C1142,532 1066,564 1090,622 C1110,670 1058,696 1078,700"
          fill="none" stroke="#eefaff" stroke-opacity=".75" stroke-width="10" stroke-linecap="round"/>
    <path class="river-flow" d="M1174,330 C1230,380 1120,420 1130,474 C1142,532 1066,564 1090,622 C1110,670 1058,696 1078,700"/>
    <path class="river-flow flow2" transform="translate(-13,2)"
          d="M1174,330 C1230,380 1120,420 1130,474 C1142,532 1066,564 1090,622 C1110,670 1058,696 1078,700"/>
    <use href="#tree" transform="translate(150,545) scale(.9)"/>
    <use href="#tree" transform="translate(265,572) scale(1.1)"/>
    <use href="#tree" transform="translate(405,560) scale(.75)"/>
    <use href="#tree" transform="translate(620,610) scale(.8)"/>
    <use href="#tree" transform="translate(1195,575) scale(1)"/>
    <use href="#tree" transform="translate(1305,592) scale(.8)"/>
    <use href="#tree" transform="translate(1385,610) scale(.65)"/>
  </svg>
</div>
<div class="sun-orb" id="sun" aria-hidden="true"></div>
<div class="moon-orb" id="moon" aria-hidden="true"></div>
<div class="wrap">
  <header class="hero">
    <div class="ico">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
    </div>
    <div>
      <h1>工时记录</h1>
      <p>休息时段不计工时：12:00~13:30 · 17:30~18:00</p>
    </div>
    <span class="tag">共用 工时记录.csv</span>
  </header>

  <div class="tips" id="tips"></div>

  <section class="card">
    <h2><span class="t"><span class="dot"></span>记录打卡</span></h2>
    <form id="f">
      <label>日期<input type="date" id="d" required></label>
      <label>签到时间<input type="time" id="s" required></label>
      <label>签退时间<input type="time" id="e" required></label>
      <button type="submit">保存</button>
    </form>
    <div id="msg" class="msg"></div>
  </section>

  <section class="card">
    <h2><span class="t"><span class="dot"></span>月度统计</span><input type="month" id="m"></h2>
    <div id="syncinfo" class="syncinfo"></div>
    <div id="stats"></div>
    <table>
      <thead><tr><th style="width:110px">日期</th><th style="width:100px">当日工时</th><th>占比</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
    <div id="empty" class="empty" style="display:none">
      <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>
      该月暂无打卡记录
    </div>
  </section>

  <footer>工时记录 · 网页版 ｜ 与控制台脚本共用同一份数据文件</footer>
</div>
<script>
const $ = id => document.getElementById(id);
const S = 'width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
const I = {
  clock:  '<svg ' + S + '><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  cal:    '<svg ' + S + '><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M8 2v4M16 2v4M3 9h18"/></svg>',
  target: '<svg ' + S + '><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/></svg>',
  check:  '<svg ' + S + '><path d="M20 6L9 17l-5-5"/></svg>',
  warn:   '<svg ' + S + '><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  up:     '<svg ' + S + '><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg>',
  down:   '<svg ' + S + '><path d="M3 7l6 6 4-4 8 8"/><path d="M21 10v7h-7"/></svg>',
  flag:   '<svg ' + S + '><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/></svg>',
  gift:   '<svg ' + S + '><polyline points="20 12 20 22 4 22 4 12"/><rect x="2" y="7" width="20" height="5"/><line x1="12" y1="22" x2="12" y2="7"/><path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"/><path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"/></svg>'
};

function today() {
  const t = new Date();
  return t.getFullYear() + '-' + String(t.getMonth() + 1).padStart(2, '0') + '-' + String(t.getDate()).padStart(2, '0');
}
function thisMonth() { return today().slice(0, 7); }
function stat(icon, k, v) {
  return '<div class="stat"><div class="k">' + icon + ' ' + k + '</div><div class="v">' + v + '</div></div>';
}
function tipHtml(cls, icon, t1, t2) {
  return '<div class="tip"><div class="tico ' + cls + '">' + icon + '</div>' +
    '<div class="tx"><div class="t1">' + t1 + '</div><div class="t2">' + t2 + '</div></div></div>';
}

async function loadTips() {
  const d = await (await fetch('/api/countdown')).json();
  if (!d.ok) return;
  let holiday;
  if (!d.holiday) {
    holiday = tipHtml('h', I.flag, '暂无节假日数据', '请更新脚本中的 HOLIDAYS 列表');
  } else if (d.holiday.ongoing) {
    holiday = tipHtml('h', I.flag, d.holiday.name + '假期进行中', '放假至 ' + d.holiday.end);
  } else {
    const n = d.holiday.days;
    const word = n === 0 ? '今天开始' : n === 1 ? '<span class="num">明天</span>开始' : '还有 <span class="num">' + n + '</span> 天';
    holiday = tipHtml('h', I.flag, '距离' + d.holiday.name + word, '假期：' + d.holiday.start + ' ~ ' + d.holiday.end);
  }
  const n = d.payday.days;
  const pay = n === 0 ? '今天发工资' : n === 1 ? '<span class="num">明天</span>发工资' : '还有 <span class="num">' + n + '</span> 天发工资';
  const payday = tipHtml('p', I.gift, pay, '发薪日：' + d.payday.date);
  $('tips').innerHTML = holiday + payday;
}

async function loadMonth() {
  const month = $('m').value || thisMonth();
  const d = await (await fetch('/api/month?month=' + month)).json();
  if (!d.ok) return;
  const s = d.sync, si = $('syncinfo');
  if (s && !s.ok) {
    si.className = 'syncinfo err';
    si.textContent = '考勤同步失败：' + s.error;
  } else if (s && s.ok && (s.synced || s.skipped)) {
    si.className = 'syncinfo ok';
    si.textContent = '已从考勤系统同步 ' + s.synced + ' 条打卡记录' + (s.skipped ? '（跳过 ' + s.skipped + ' 条不完整记录）' : '');
  } else {
    si.className = 'syncinfo';
    si.textContent = '';
  }
  const max = Math.max(10, ...d.days.map(x => x.hours));
  $('rows').innerHTML = d.days.map(x =>
    '<tr><td class="date">' + x.date + '</td>' +
    '<td class="hours' + (x.hours >= 8 ? ' good' : '') + '">' + x.hours.toFixed(2) + ' <small>小时</small></td>' +
    '<td><div class="bar"><i data-w="' + Math.min(100, x.hours / max * 100).toFixed(1) + '" class="' + (x.hours >= 8 ? 'over' : '') + '"></i></div></td></tr>'
  ).join('');
  $('empty').style.display = d.days.length ? 'none' : 'block';
  $('stats').innerHTML = d.days.length ? (
    '<div class="stats">' +
    stat(I.clock, '总工时', d.total.toFixed(2) + ' <small>小时</small>') +
    stat(I.cal, '出勤 / 日历工作日', d.attendance + ' <small>/ ' + d.required_days + ' 天</small>') +
    stat(I.target, '标准 8h/天', d.required.toFixed(2) + ' <small>小时</small>') +
    stat(d.satisfied ? I.check : I.warn, '判定',
      '<span class="badge ' + (d.satisfied ? 'ok' : 'bad') + '">' + (d.satisfied ? '满足' : '不满足') + '</span>') +
    stat(d.satisfied ? I.up : I.down, (d.satisfied ? '超出' : '欠') + '工时',
      '<span class="' + (d.satisfied ? 'good' : 'warn') + '">' + (d.satisfied ? '+' : '-') + Math.abs(d.diff).toFixed(2) + '</span><small> 小时</small>') +
    stat(I.up, '满8小时天数', '<span class="num">' + (d.over8_days || 0) + '</span><small> 天</small>') +
    '</div>') : '';
  const bars = document.querySelectorAll('.bar > i');
  requestAnimationFrame(() => requestAnimationFrame(() =>
    bars.forEach(b => { b.style.width = b.dataset.w + '%'; })));
}

$('f').addEventListener('submit', async ev => {
  ev.preventDefault();
  const msg = $('msg');
  const d = await (await fetch('/api/record', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ date: $('d').value, start: $('s').value, end: $('e').value })
  })).json();
  if (!d.ok) { msg.className = 'msg err'; msg.textContent = d.error || '保存失败'; return; }
  msg.className = 'msg ok';
  msg.textContent = '已保存 ' + d.date + '：' + d.day_hours.toFixed(2) + ' 小时 ｜ 该月总工时 ' + d.total.toFixed(2) +
    ' 小时，出勤 ' + d.attendance + ' 天（日历工作日 ' + d.required_days + ' 天），' +
    (d.satisfied ? '工时满足条件，超出 ' : '工时不满足，还欠 ') + Math.abs(d.diff).toFixed(2) + ' 小时';
  $('m').value = d.date.slice(0, 7);
  loadMonth();
});

$('m').addEventListener('change', loadMonth);
$('d').value = today();
$('m').value = thisMonth();
loadTips();
loadMonth();

/* ===== 日月弧线运动与昼夜切换 ===== */
(function () {
  const sun = document.getElementById('sun');
  const moon = document.getElementById('moon');
  const DAY_START = 9 * 60;        // 09:00
  const NOON = 12 * 60;            // 12:00
  const DAY_END = 17 * 60 + 30;    // 17:30
  const NIGHT_LEN = 1440 - DAY_END + DAY_START;

  function minutesNow() {
    const d = new Date();
    return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60;
  }
  function isNight(m) { return m >= DAY_END || m < DAY_START; }
  function sunT(m) {
    if (m < DAY_START || m > DAY_END) return null;
    return m <= NOON
      ? 0.5 * (m - DAY_START) / (NOON - DAY_START)
      : 0.5 + 0.5 * (m - NOON) / (DAY_END - NOON);
  }
  function moonT(m) {
    if (!isNight(m)) return null;
    const passed = m >= DAY_END ? m - DAY_END : m + (1440 - DAY_END);
    return passed / NIGHT_LEN;
  }
  function arc(t) {
    const pad = Math.max(36, innerWidth * 0.05);
    const x = innerWidth - pad - (innerWidth - pad * 2) * t;
    const apex = Math.max(64, innerHeight * 0.09);
    const end = Math.max(apex + 80, innerHeight * 0.24);
    const y = apex + (end - apex) * 4 * Math.pow(t - 0.5, 2);
    return { x: x, y: y };
  }
  function place(el, t) {
    if (t === null) { el.style.opacity = '0'; return; }
    const p = arc(t);
    el.style.left = p.x + 'px';
    el.style.top = p.y + 'px';
    el.style.opacity = '1';
  }
  function tick() {
    const m = minutesNow();
    document.body.classList.toggle('night', isNight(m));
    place(sun, sunT(m));
    place(moon, moonT(m));
    requestAnimationFrame(tick);
  }
  tick();
})();
</script>
</body>
</html>
"""


_last_sync = 0.0
_last_sync_result = None
SYNC_MIN_INTERVAL = 60  # 两次同步的最小间隔（秒），避免频繁请求考勤接口


def _sync_in_process():
    """打包成 exe 后 sys.executable 是 exe 自身，无法再用它运行子脚本，
    改为在本进程内以后台线程执行 read_work_hours_ui.py 的抓取逻辑"""
    global _last_sync_result

    def _worker():
        global _last_sync_result
        try:
            mod = _load_module("gongshi_sync", "read_work_hours_ui.py")
            _last_sync_result = {"ok": True, "synced": mod.run(), "skipped": 0}
        except SystemExit as e:
            _last_sync_result = {"ok": False, "error": str(e)}
        except Exception as e:
            _last_sync_result = {"ok": False, "error": str(e)}

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(120)
    if t.is_alive():
        _last_sync_result = {"ok": False, "error": "抓取超时（120 秒），可能需要在浏览器中重新登录"}
    return _last_sync_result


def sync_from_attendance(force=False):
    """后台运行 read_work_hours_ui.py 抓取打卡记录并写入 工时记录.csv

    UI 脚本以无窗口方式运行 Edge，自动点击"打卡记录"并解析弹窗中的
    日期/签到/签退，算好工时写入 CSV；同日期以抓取数据为准（覆盖）。
    登录态失效时会抓取失败，需手动运行一次：
    python read_work_hours_ui.py --show
    """
    global _last_sync, _last_sync_result
    now = time.time()
    if not force and now - _last_sync < SYNC_MIN_INTERVAL:
        return _last_sync_result
    _last_sync = now
    if getattr(sys, "frozen", False):
        return _sync_in_process()
    ui = os.path.join(SCRIPT_DIR, "read_work_hours_ui.py")
    if not os.path.exists(ui):
        _last_sync_result = {"ok": False, "error": "未找到 read_work_hours_ui.py"}
        return _last_sync_result
    try:
        r = subprocess.run([sys.executable, ui], timeout=120, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if r.returncode != 0:
            lines = [x for x in (r.stderr or "").strip().splitlines() if x.strip()]
            _last_sync_result = {"ok": False,
                                 "error": lines[-1] if lines else f"退出码 {r.returncode}"}
        else:
            m = re.search(r"共 (\d+) 条", r.stdout or "")
            _last_sync_result = {"ok": True, "synced": int(m.group(1)) if m else 0,
                                 "skipped": 0}
    except subprocess.TimeoutExpired:
        _last_sync_result = {"ok": False, "error": "抓取超时（120 秒），可能需要在浏览器中重新登录"}
    except Exception as e:
        _last_sync_result = {"ok": False, "error": str(e)}
    return _last_sync_result


def month_stats(month):
    try:
        year, m = (int(x) for x in month.split("-"))
        date(year, m, 1)
    except (ValueError, TypeError):
        return {"ok": False, "error": "无效的月份，应为如 2026-09 的格式"}
    prefix = f"{year:04d}-{m:02d}-"
    days = {}
    for r in core.load_rows():
        if r["日期"].startswith(prefix):
            days[r["日期"]] = float(r["今日工时"])
    total = sum(days.values())
    attendance = sum(1 for h in days.values() if h > 0)
    required_days = core.workdays_in_month(year, m)
    required, diff = core.judge(total, attendance)
    over8_days = sum(1 for h in days.values() if h >= 8)
    return {
        "ok": True,
        "month": prefix[:7],
        "days": [{"date": d, "hours": days[d]} for d in sorted(days)],
        "total": round(total, 2),
        "attendance": attendance,
        "required_days": required_days,
        "required": round(required, 2),
        "diff": round(diff, 2),
        "satisfied": diff >= 0,
        "over8_days": over8_days,
        "sync": _last_sync_result,
    }


def save_record(date_str, start, end):
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return {"ok": False, "error": "无效的日期"}
    if target > date.today():
        return {"ok": False, "error": "不能记录未来的日期"}
    try:
        minutes = core.calc_work_minutes(core.parse_time(start), core.parse_time(end))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    day_hours = minutes / 60
    target_str = target.strftime("%Y-%m-%d")
    # 只统计截至目标日期的记录 → 该行写入的是"截至当天"的累计快照，日后不被新记录刷新
    _, total, attendance = core.build_stats(core.load_rows(), target_str, day_hours, until=target_str)
    required_days = core.workdays_in_month(target.year, target.month)
    required, diff = core.judge(total, attendance)
    core.upsert_row(target_str, total, required_days, attendance, day_hours, diff)
    return {
        "ok": True,
        "date": target_str,
        "day_hours": round(day_hours, 2),
        "total": round(total, 2),
        "attendance": attendance,
        "required_days": required_days,
        "required": round(required, 2),
        "diff": round(diff, 2),
        "satisfied": diff >= 0,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, content_type, body):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, "text/html; charset=utf-8", HTML)
        elif parsed.path == "/api/month":
            month = (parse_qs(parsed.query).get("month") or [""])[0].strip()
            self._json(month_stats(month))
        elif parsed.path == "/api/countdown":
            self._json(countdown_info())
        else:
            self._send(404, "text/plain; charset=utf-8", "Not Found")

    def do_POST(self):
        if urlparse(self.path).path != "/api/record":
            self._send(404, "text/plain; charset=utf-8", "Not Found")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = save_record(str(payload.get("date", "")), str(payload.get("start", "")), str(payload.get("end", "")))
            self._json(result, code=200 if result.get("ok") else 400)
        except Exception as e:  # 兜底，避免服务崩溃
            self._json({"ok": False, "error": f"服务器处理失败：{e}"}, code=500)

    def log_message(self, *args):  # 静默访问日志
        pass


def main():
    core.ensure_migrated()   # 旧格式文件自动迁移为按月分区
    # 先在后台抓取打卡记录再启动服务（数据就绪后浏览器打开即可看到最新数据）
    print("正在后台抓取打卡记录（无窗口运行，最长等 2 分钟）…")
    r = sync_from_attendance(force=True) or {}
    if r.get("ok"):
        print(f"抓取完成：{r.get('synced', 0)} 条打卡记录已写入 工时记录.csv")
    else:
        print(f"抓取失败：{r.get('error')}（不影响本地数据使用）")
    server = None
    port = None
    for p in range(8765, 8776):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if server is None:
        print("无法启动：8765~8775 端口均被占用")
        return
    url = f"http://127.0.0.1:{port}"
    print(f"工时记录网页已启动：{url}（按 Ctrl+C 停止）")
    threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
