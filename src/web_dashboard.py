"""Web 仪表盘 — 本地 HTTP 服务，提供可视化工作状态面板和 API

启动后在浏览器打开 http://localhost:8765 查看仪表盘.
"""

import json
import logging
import re
import threading
from datetime import date, datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── HTML 仪表盘页面 ──────────────────────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Work Reporter</title>
<style>
  :root {
    --bg: #f3f1ea; --surface: #fffefb; --surface2: #f7f6f0;
    --text: #28251f; --text2: #6c685f; --faint: #9a958b;
    --border: #e3ded3; --hair: #ece8df;
    --accent: #c05e3e; --accent-hover: #a9512f; --accent-soft: #f5ece4;
    --success: #3a7d5a; --warn: #c4982f; --danger: #bb5440;
    --font: 'Inter','Microsoft YaHei','PingFang SC',sans-serif;
    --radius: 14px; --radius-sm: 10px;
    --cat-code: #3b6fb6; --cat-doc: #2a8c4e; --cat-comm: #c07a20;
    --cat-browse: #7b3fa3; --cat-meeting: #c0392b; --cat-design: #16806d;
    --cat-learn: #5b4cc4; --cat-misc: #6b7280; --cat-other: #8a8a8a;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:var(--font); background:var(--bg); color:var(--text); min-height:100vh; line-height:1.5; }
  .header { background:var(--surface); padding:18px 32px; border-bottom:1px solid var(--hair); display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
  .header h1 { font-size:18px; font-weight:600; letter-spacing:-0.3px; }
  .header .subtitle { color:var(--faint); font-size:12px; }
  .container { max-width:1440px; margin:0 auto; padding:20px 24px; }
  .stats-bar { display:flex; gap:0; margin-bottom:18px; background:var(--surface); border-radius:var(--radius); border:1px solid var(--border); overflow:hidden; }
  .stats-bar .stat { flex:1; padding:14px 16px; text-align:center; border-right:1px solid var(--hair); }
  .stats-bar .stat:last-child { border-right:none; }
  .stats-bar .stat .label { font-size:11px; color:var(--faint); text-transform:uppercase; letter-spacing:0.5px; }
  .stats-bar .stat .value { font-size:24px; font-weight:700; margin-top:2px; }
  .actions { display:flex; gap:8px; margin-bottom:18px; flex-wrap:wrap; }
  .btn { padding:8px 14px; border-radius:8px; border:1px solid var(--border); cursor:pointer; font-size:12px; font-weight:600; transition:all 0.14s; font-family:var(--font); background:var(--surface); color:var(--text); display:flex; align-items:center; gap:4px; }
  .btn:hover { border-color:var(--accent); color:var(--accent); }
  .btn-accent { background:var(--accent); border-color:var(--accent); color:#fff; }
  .btn-accent:hover { background:var(--accent-hover); color:#fff; }
  .btn-sm { padding:5px 10px; font-size:11px; }
  .panel { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:18px; }
  .main-grid { display:grid; grid-template-columns:minmax(0,1.2fr) minmax(360px,0.8fr); gap:18px; align-items:start; }
  @media (max-width:860px) { .main-grid { grid-template-columns:1fr; } }

  .timeline { } /* no max-height */
  .event-item { display:flex; align-items:flex-start; gap:10px; padding:9px 12px; border-left:3px solid transparent; margin:1px 0; border-radius:0 8px 8px 0; transition:background 0.12s; }
  .event-item:hover { background:var(--surface2); }
  .event-item.cat-code { border-left-color:var(--cat-code); }
  .event-item.cat-doc { border-left-color:var(--cat-doc); }
  .event-item.cat-comm { border-left-color:var(--cat-comm); }
  .event-item.cat-browse { border-left-color:var(--cat-browse); }
  .event-item.cat-meeting { border-left-color:var(--cat-meeting); }
  .event-item.cat-design { border-left-color:var(--cat-design); }
  .event-item.cat-learn { border-left-color:var(--cat-learn); }
  .event-item.cat-misc { border-left-color:var(--cat-misc); }
  .event-item.cat-other { border-left-color:var(--cat-other); }
  .event-time { min-width:64px; font-size:15px; font-weight:600; font-family:monospace; color:var(--text); font-variant-numeric:tabular-nums; }
  .event-gap { min-width:64px; text-align:center; font-size:12px; color:var(--faint); }
  .event-body { flex:1; min-width:0; }
  .event-title { font-weight:600; font-size:13px; word-break:break-word; }
  .event-desc { color:var(--text2); font-size:12px; margin-top:3px; line-height:1.5; word-break:break-word; }
  .event-footer { display:flex; gap:6px; align-items:center; margin-top:5px; flex-wrap:wrap; }
  .cat-select { font-size:11px; padding:2px 5px; border-radius:4px; background:var(--surface2); color:var(--text2); border:1px solid var(--border); cursor:pointer; font-family:var(--font); }
  .cat-select:focus { outline:2px solid var(--accent-soft); border-color:var(--accent); }
  .project-tag { font-size:11px; color:var(--accent); background:var(--accent-soft); padding:1px 6px; border-radius:4px; }
  .badge { display:inline-block; padding:1px 7px; border-radius:4px; font-size:10px; font-weight:600; }
  .badge-code { background:rgba(59,111,182,0.12); color:var(--cat-code); }
  .badge-doc { background:rgba(42,140,78,0.12); color:var(--cat-doc); }
  .badge-comm { background:rgba(192,122,32,0.12); color:var(--cat-comm); }
  .badge-browse { background:rgba(123,63,163,0.12); color:var(--cat-browse); }
  .badge-meeting { background:rgba(192,57,43,0.12); color:var(--cat-meeting); }
  .badge-design { background:rgba(22,128,109,0.12); color:var(--cat-design); }
  .badge-learn { background:rgba(91,76,196,0.12); color:var(--cat-learn); }
  .badge-misc { background:rgba(107,114,128,0.12); color:var(--cat-misc); }
  .badge-other { background:rgba(138,138,138,0.12); color:var(--cat-other); }

  .heatmap-container { margin-bottom:14px; }
  .heatmap-legend { display:flex; gap:10px; margin-bottom:8px; font-size:11px; color:var(--text2); flex-wrap:wrap; }
  .heatmap-legend .legend-item { display:flex; align-items:center; gap:4px; }
  .heatmap-legend .legend-swatch { width:10px; height:10px; border-radius:2px; }
  .heatmap-bars { display:flex; gap:4px; align-items:flex-end; }
  .heatmap-bar-col { display:flex; flex-direction:column; align-items:center; }
  .heatmap-bar { width:18px; height:120px; display:flex; flex-direction:column-reverse; border-radius:2px; overflow:hidden; outline:1px solid var(--hair); flex-shrink:0; }
  .heatmap-seg { width:100%; flex-shrink:0; transition:opacity 0.2s; }
  .heatmap-seg:hover { opacity:0.7; }
  .heatmap-label { font-size:9px; color:var(--faint); margin-top:3px; font-family:monospace; }

  .tab-bar { display:flex; gap:0; margin-bottom:12px; border-bottom:1px solid var(--hair); }
  .tab-btn { padding:7px 14px; font-size:12px; font-weight:600; cursor:pointer; border:none; background:none; color:var(--faint); border-bottom:2px solid transparent; margin-bottom:-1px; transition:all 0.15s; font-family:var(--font); }
  .tab-btn:hover { color:var(--text); }
  .tab-btn.active { color:var(--accent); border-bottom-color:var(--accent); }

  .report-panel { position:sticky; top:20px; }
  .report-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; }
  .report-header h2 { font-size:15px; font-weight:600; }
  .report-body { font-size:13px; line-height:1.7; }
  .report-body h1 { font-size:20px; margin:16px 0 8px; font-weight:600; }
  .report-body h2 { font-size:16px; margin:14px 0 6px; font-weight:600; }
  .report-body h3 { font-size:14px; margin:10px 0 4px; font-weight:600; }
  .report-body p { margin:6px 0; }
  .report-body ul, .report-body ol { margin:4px 0 8px 20px; }
  .report-body li { margin:2px 0; }
  .report-body code { background:var(--surface2); padding:1px 5px; border-radius:3px; font-size:12px; }
  .report-body pre { background:var(--surface2); padding:10px; border-radius:8px; font-size:12px; overflow-x:auto; margin:8px 0; }
  .report-body table { border-collapse:collapse; width:100%; margin:8px 0; font-size:12px; }
  .report-body th, .report-body td { border:1px solid var(--border); padding:6px 10px; text-align:left; }
  .report-body th { background:var(--surface2); font-weight:600; }
  .report-body strong { font-weight:600; }
  .report-body blockquote { border-left:3px solid var(--accent); padding:4px 12px; margin:8px 0; color:var(--text2); }

  .empty-state { min-height:80px; border:1px dashed var(--border); border-radius:var(--radius-sm); display:grid; place-items:center; color:var(--faint); text-align:center; padding:20px; font-size:13px; }
  .empty { text-align:center; color:var(--faint); padding:32px; font-size:13px; }

  .status-dot { width:7px; height:7px; border-radius:50%; display:inline-block; margin-right:6px; }
  .status-dot.active { background:var(--success); }
  .status-dot.paused { background:var(--warn); }
  .toast { position:fixed; top:20px; right:20px; background:var(--surface); border:1px solid var(--border); padding:10px 18px; border-radius:var(--radius-sm); font-size:13px; z-index:1000; box-shadow:0 4px 16px rgba(0,0,0,0.08); animation:slideIn 0.3s ease; }
  @keyframes slideIn { from { transform:translateX(100%); opacity:0; } to { transform:translateX(0); opacity:1; } }

  .llm-event { background:var(--surface2); border:1px solid var(--hair); border-radius:var(--radius-sm); padding:12px; margin-bottom:10px; }
  .llm-event-header { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
  .llm-event-header .time { color:var(--accent); font-family:monospace; font-size:12px; }
  .llm-event-header .activity { font-weight:600; font-size:13px; flex:1; }
  .llm-raw { font-family:'Cascadia Code','Fira Code',monospace; font-size:11px; line-height:1.5; background:var(--surface2); border-radius:6px; padding:10px; white-space:pre-wrap; word-break:break-word; max-height:300px; overflow-y:auto; color:var(--text2); }
  .llm-no-data { text-align:center; color:var(--faint); padding:20px; font-size:13px; }
  .llm-meta { font-size:11px; color:var(--faint); margin-bottom:6px; }
  ::-webkit-scrollbar { width:5px; } ::-webkit-scrollbar-track { background:transparent; } ::-webkit-scrollbar-thumb { background:var(--border); border-radius:3px; }
</style>
</head>
<body>
<div class="header">
  <div><h1>Work Reporter</h1><div class="subtitle" id="statusText">加载中...</div></div>
  <div class="actions" style="margin-bottom:0;">
    <button class="btn" onclick="apiPost('/api/capture')">📸 截屏</button>
    <button class="btn" onclick="apiPost('/api/pause')">⏯ 暂停</button>
    <button class="btn" id="btnAuto" onclick="toggleAuto()">🔄 自动</button>
  </div>
</div>
<div class="container">
  <div class="stats-bar">
    <div class="stat"><div class="label">今日截图</div><div class="value" style="color:var(--accent)" id="ssCount">-</div></div>
    <div class="stat"><div class="label">今日事件</div><div class="value" style="color:var(--cat-code)" id="evtCount">-</div></div>
    <div class="stat"><div class="label">隐私过滤</div><div class="value" style="color:var(--success)" id="privSkip">-</div></div>
    <div class="stat"><div class="label">截屏状态</div><div class="value" style="color:var(--warn);font-size:14px" id="pauseStatus">-</div></div>
    <div class="stat"><div class="label">日报</div><div class="value" style="font-size:14px" id="dailyStatus">-</div></div>
  </div>

  <div class="main-grid">
    <div class="left-col">
      <div class="panel">
        <div class="tab-bar">
          <button class="tab-btn active" onclick="switchTab('timeline')" id="tab-timeline">活动时间线</button>
          <button class="tab-btn" onclick="switchTab('llm')" id="tab-llm">LLM 原始输出</button>
        </div>
        <div class="heatmap-container" id="heatmapBar"></div>
        <div class="timeline" id="eventList"><div class="empty">暂无今日事件，按 <kbd>%HOTKEY%</kbd> 开始截屏</div></div>
        <div id="llmPanel" style="display:none;"><div class="llm-no-data">加载中...</div></div>
      </div>
    </div>

    <div class="right-col">
      <div class="panel report-panel">
        <div class="report-header">
          <h2>📋 今日报告</h2>
          <button class="btn btn-accent btn-sm" onclick="generateReport()">生成日报</button>
        </div>
        <div id="reportContent" class="report-body">
          <div class="empty-state">点击「生成日报」创建今天的工作报告</div>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
const API = '/api';
function $(id) { return document.getElementById(id); }
async function fetchJSON(url) { const r = await fetch(url); return r.json(); }
async function apiPost(path) {
  try { const r=await fetch(API+path,{method:'POST'}); const d=await r.json(); showToast(d.message||'OK'); refresh(); }
  catch(e) { showToast('Error: '+e.message); }
}
async function toggleAuto() {
  try {
    var r = await fetch(API+'/toggle-auto', {method:'POST'});
    var d = await r.json();
    showToast(d.message);
    refresh();
  } catch(e) { showToast('Error: '+e.message); }
}
function showToast(msg) { const t=document.createElement('div'); t.className='toast'; t.textContent=msg; document.body.appendChild(t); setTimeout(()=>t.remove(),3000); }

const CAT_ICONS = { '创作构建':'🛠','阅读查阅':'📖','沟通协作':'💬','分析计算':'📊','会议讨论':'🎙','设计绘图':'🎨','学习研究':'🔬','娱乐休闲':'🎮','其他':'📌' };
const CAT_CSS = { '创作构建':'cat-code','阅读查阅':'cat-doc','沟通协作':'cat-comm','分析计算':'cat-browse','会议讨论':'cat-meeting','设计绘图':'cat-design','学习研究':'cat-learn','娱乐休闲':'cat-other','其他':'cat-misc' };

function catIcon(cat) { return CAT_ICONS[cat]||'📌'; }
function catCss(cat) { return CAT_CSS[cat]||'cat-other'; }
function badgeClass(cat) { var css = CAT_CSS[cat]; return 'badge badge-' + (css ? css.replace('cat-','') : 'other'); }

function fmtTime(ts) {
  if (!ts) return '--:--';
  return new Date(ts).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
}
function hourOf(ts) { return ts ? new Date(ts).getHours() : 0; }

function renderTimeline(events) {
  var html = '';
  if (events.length === 0) return html;

  var CATS = ['创作构建','阅读查阅','沟通协作','分析计算','会议讨论','设计绘图','学习研究','娱乐休闲','其他'];

  var rev = events.slice().reverse();
  var lastTime = null;

  rev.forEach(function(e) {
    var cat = (e.category && e.category in CAT_CSS) ? e.category : '其他';
    var cls = catCss(cat);
    var t = fmtTime(e.timestamp);

    // 时间间隔
    var gapHtml = '';
    if (lastTime) {
      var diffMin = Math.round((lastTime - new Date(e.timestamp))/60000);
      if (diffMin >= 5) {
        var gapText = diffMin >= 60 ? Math.round(diffMin/60)+'h' : diffMin+'m';
        gapHtml = '<div class="event-gap">↓'+gapText+'</div>';
      } else {
        gapHtml = '<div class="event-gap"></div>';
      }
    }
    lastTime = new Date(e.timestamp);

    // detail：过滤 VLM 失败前缀
    var desc = e.detail || '';
    if (/^(由于|因为|VLM|The image|This image|截图|Unable|Cannot)/i.test(desc)) {
      desc = '';
    }

    // category 下拉选项
    var catOpts = '';
    CATS.forEach(function(c) {
      catOpts += '<option value="'+c+'"'+(c===cat?' selected':'')+'>'+c+'</option>';
    });

    html += '<div class="event-item '+cls+'">';
    html += '<div class="event-time">'+t+'</div>';
    html += gapHtml;
    html += '<div class="event-body">';
    html += '<div class="event-title">'+(e.activity||'未记录')+'</div>';
    if (desc) html += '<div class="event-desc">'+desc+'</div>';
    html += '<div class="event-footer">';
    html += '<select class="cat-select" onchange="changeCategory('+e.id+',this.value)">'+catOpts+'</select>';
    if (e.project) html += '<span class="project-tag">'+e.project+'</span>';
    html += '</div></div></div>';
  });

  return html;
}

async function changeCategory(id, cat) {
  try {
    await fetch(API+'/event/'+id+'/category', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({category: cat})
    });
    refresh();
  } catch(e) { console.error(e); }
}

// 类别到颜色的映射（与 CSS 变量 --cat-* 保持一致）
var CAT_COLORS = {
  '创作构建': '#4A90D9',
  '阅读查阅': '#2ecc71',
  '沟通协作': '#f39c12',
  '分析计算': '#9b59b6',
  '会议讨论': '#e74c3c',
  '设计绘图': '#1abc9c',
  '学习研究': '#7B68EE',
  '娱乐休闲': '#95a5a6',
  '其他': '#8899aa',
};
function getCatColor(cat) { return CAT_COLORS[cat] || '#8899aa'; }

function renderHeatmap(events) {
  var DEFAULT_MIN = 5;   // 最后一个事件的默认时长（分钟）
  var MAX_GAP = 60;      // 事件间最大时长上限（分钟）
  var MAX_BAR_H = 120;   // 柱状条最大高度（px）
  var MIN_SEG_H = 2;     // 分段最小高度（px）

  // 1. 按小时×类别累计时长
  var hourData = {};
  var allCats = {};
  for (var h = 0; h < 24; h++) { hourData[h] = {}; }

  for (var i = 0; i < events.length; i++) {
    var e = events[i];
    if (!e.timestamp) continue;
    var ts = new Date(e.timestamp);
    var hour = ts.getHours();
    var cat = e.category || '其他';
    allCats[cat] = true;

    // 计算时长：到下一事件的时间差，上限 MAX_GAP
    var duration;
    if (i + 1 < events.length && events[i + 1].timestamp) {
      var nextTs = new Date(events[i + 1].timestamp);
      duration = Math.min((nextTs - ts) / 60000, MAX_GAP);
    } else {
      duration = DEFAULT_MIN;
    }
    if (duration <= 0) duration = 1;

    hourData[hour][cat] = (hourData[hour][cat] || 0) + duration;
  }

  // 2. 找出所有小时中的最大累计时长（用于归一化）
  var maxTotal = 0;
  for (var h = 0; h < 24; h++) {
    var sum = 0;
    for (var c in hourData[h]) { sum += hourData[h][c]; }
    if (sum > maxTotal) maxTotal = sum;
  }
  if (maxTotal === 0) maxTotal = 1;

  // 3. 类别排序（保证各柱颜色顺序一致）
  var catList = Object.keys(allCats).sort();

  // 4. 渲染图例
  var html = '<div class="heatmap-legend">';
  catList.forEach(function(cat) {
    html += '<div class="legend-item">';
    html += '<div class="legend-swatch" style="background:' + getCatColor(cat) + '"></div>';
    html += '<span>' + cat + '</span>';
    html += '</div>';
  });
  html += '</div>';

  // 5. 渲染 24 根柱状条
  html += '<div class="heatmap-bars">';
  for (var h = 0; h < 24; h++) {
    var totalH = 0;
    for (var c in hourData[h]) { totalH += hourData[h][c]; }

    html += '<div class="heatmap-bar-col">';
    html += '<div class="heatmap-bar">';

    if (totalH > 0) {
      catList.forEach(function(cat) {
        var mins = hourData[h][cat] || 0;
        if (mins > 0) {
          var segH = Math.max(MIN_SEG_H, Math.round((mins / maxTotal) * MAX_BAR_H));
          var tip = String(h).padStart(2, '0') + ':00 — ' + cat + ': ' + Math.round(mins) + ' 分钟';
          html += '<div class="heatmap-seg" style="height:' + segH + 'px; background:' + getCatColor(cat) + ';" title="' + tip + '"></div>';
        }
      });
    }

    html += '</div>';
    html += '<div class="heatmap-label">' + String(h).padStart(2, '0') + '</div>';
    html += '</div>';
  }
  html += '</div>';

  $('heatmapBar').innerHTML = html;
}

var currentTab = 'timeline';
var llmEventsCache = [];

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab-btn').forEach(function(b){ b.classList.remove('active'); });
  document.getElementById('tab-'+tab).classList.add('active');
  if (tab === 'timeline') {
    $('eventList').style.display = '';
    $('llmPanel').style.display = 'none';
    $('heatmapBar').style.display = '';
  } else {
    $('eventList').style.display = 'none';
    $('llmPanel').style.display = '';
    $('heatmapBar').style.display = 'none';
    if (llmEventsCache.length > 0) renderLLMOutput(llmEventsCache);
  }
}

function renderMarkdown(md) {
  if (!md) return '<div class="empty-state">暂无内容</div>';
  // Escape HTML first
  md = md.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  // Headers
  md = md.replace(/^### (.+)$/gm,'<h3>$1</h3>');
  md = md.replace(/^## (.+)$/gm,'<h2>$1</h2>');
  md = md.replace(/^# (.+)$/gm,'<h1>$1</h1>');
  // Bold / italic
  md = md.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  md = md.replace(/\*(.+?)\*/g,'<em>$1</em>');
  // Inline code
  md = md.replace(/`([^`]+)`/g,'<code>$1</code>');
  // Horizontal rule
  md = md.replace(/^---$/gm,'<hr>');
  // Tables (simple: header row + separator row + data rows)
  md = md.replace(/(^\|.+\|$\n^\|[-| :]+\|$\n(?:^\|.+\|$\n?)*)/gm, function(m){
    var rows = m.trim().split('\n');
    var html = '<table>';
    rows.forEach(function(row,i){
      if (i===1) return; // skip separator
      var cells = row.replace(/^\||\|$/g,'').split('|');
      var tag = i===0 ? 'th' : 'td';
      html += '<tr>'+cells.map(function(c){return '<'+tag+'>'+c.trim()+'</'+tag+'>'}).join('')+'</tr>';
    });
    return html+'</table>';
  });
  // Unordered lists
  md = md.replace(/((?:^- .+$\n?)+)/gm, function(m){
    return '<ul>'+m.trim().split('\n').map(function(l){return '<li>'+l.replace(/^- /,'')+'</li>'}).join('')+'</ul>';
  });
  // Paragraphs: blank-line-separated blocks
  var blocks = md.split(/\n\n+/);
  return blocks.map(function(b){
    b = b.trim(); if (!b) return '';
    if (/^<(h[1-3]|table|ul|ol|hr)/.test(b)) return b;
    return '<p>'+b.replace(/\n/g,'<br>')+'</p>';
  }).join('\n');
}

async function generateReport() {
  $('reportContent').innerHTML = '<div class="empty-state" style="color:var(--accent)">生成中...</div>';
  try {
    var r = await fetch(API+'/report/daily', {method:'POST'});
    var d = await r.json();
    if (d.success && d.content) {
      $('reportContent').innerHTML = renderMarkdown(d.content);
      $('dailyStatus').textContent = '✅ 已生成';
    } else {
      $('reportContent').innerHTML = '<div class="empty-state">生成失败: '+(d.message||'未知错误')+'</div>';
    }
  } catch(e) {
    $('reportContent').innerHTML = '<div class="empty-state">请求失败: '+e.message+'</div>';
  }
}

function renderLLMOutput(events) {
  llmEventsCache = events;
  if (currentTab !== 'llm') return;

  var hasLLM = events.filter(function(e){ return e.has_llm; });
  var html = '';

  if (events.length === 0) {
    html = '<div class="llm-no-data">暂无今日事件</div>';
  } else if (hasLLM.length === 0) {
    html = '<div class="llm-no-data">暂无 LLM 分析数据（可能 LLM 未启用或全部走规则引擎兜底）</div>';
  }

  hasLLM.forEach(function(e) {
    var catIcon = CAT_ICONS[e.category]||'📌';
    var bcls = badgeClass(e.category||'其他');

    // 格式化 raw_response：尝试提取 JSON 并美化
    var rawDisplay = e.raw_response || '(空)';
    // 限制显示长度，避免页面卡顿
    if (rawDisplay.length > 5000) rawDisplay = rawDisplay.substring(0,5000) + '\n\n... (truncated, total ' + e.raw_response.length + ' chars)';

    html += '<div class="llm-event">';
    html += '<div class="llm-event-header">';
    html += '<span class="time">'+e.time+'</span>';
    html += '<span class="activity">'+catIcon+' '+e.activity+'</span>';
    html += '<span class="'+bcls+'">'+e.category+'</span>';
    if (e.project) html += '<span class="project-tag">'+e.project+'</span>';
    html += '</div>';

    if (e.activity) {
      html += '<div class="llm-meta">🎯 ' + e.activity + '</div>';
    }

    html += '<div class="llm-raw">'+rawDisplay.replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</div>';
    html += '</div>';
  });

  $('llmPanel').innerHTML = html || '<div class="llm-no-data">暂无 LLM 分析数据</div>';
}

async function refresh() {
  try {
    var status = await fetchJSON(API+'/status');
    $('statusText').innerHTML = '<span class="status-dot '+(status.is_paused?'paused':'active')+'"></span>'+status.status_text;
    $('ssCount').textContent = status.today_screenshots;
    $('evtCount').textContent = status.today_events;
    $('privSkip').textContent = status.privacy_skips;
    $('pauseStatus').textContent = status.is_paused ? '已暂停' : (status.is_auto ? '自动 ('+status.auto_interval+'min)' : '手动');
    var ba = $('btnAuto');
    if (ba) {
      ba.textContent = status.is_auto ? '🔄 自动中' : '🔄 自动';
      ba.className = status.is_auto ? 'btn btn-accent' : 'btn';
    }
    $('dailyStatus').textContent = status.has_daily_report ? '✅ 已生成' : '⏳ 未生成';

    var events = await fetchJSON(API+'/events/today');
    renderHeatmap(events);
    if (events.length > 0) {
      $('eventList').innerHTML = renderTimeline(events);
    } else {
      $('eventList').innerHTML = '<div class="empty">暂无今日事件，按快捷键开始截屏 📸</div>';
    }

    // 加载已有日报
    if (status.has_daily_report && $('reportContent').querySelector('.empty-state')) {
      fetchJSON(API+'/report/daily/today').then(function(d){
        if (d.content) $('reportContent').innerHTML = renderMarkdown(d.content);
      }).catch(function(){});
    }

    // 预加载 LLM 原始输出数据
    fetchJSON(API+'/events/today/llm').then(function(llmEvents) {
      renderLLMOutput(llmEvents);
    }).catch(function(){});
  } catch(e) { console.error(e); $('statusText').textContent = '⚠ 连接失败'; }
}

refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>"""


# ── HTTP 请求处理器 ──────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    """仪表盘 HTTP 请求处理器."""

    # 类变量，由 WebDashboard 设置
    app_ref: Any = None

    def log_message(self, format, *args):
        """重定向到 logger."""
        logger.debug("HTTP %s", format % args)

    def _send_json(self, data: Any, status: int = 200) -> None:
        """发送 JSON 响应."""
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200) -> None:
        """发送 HTML 响应."""
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_404(self) -> None:
        self._send_json({"error": "Not Found"}, 404)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    # ── 路由 ──────────────────────────────────────────

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        routes = {
            "/": self._serve_dashboard,
            "/api/status": self._api_status,
            "/api/events/today": self._api_events_today,
            "/api/events/today/llm": self._api_events_today_llm,
            "/api/events/recent": self._api_events_recent,
            "/api/report/daily/today": self._api_report_today,
            "/api/stats": self._api_stats,
            "/api/reports": self._api_reports,
        }

        # 静态文件服务 — 报告内容
        if path.startswith("/reports/"):
            self._serve_report_file(path)
            return

        handler = routes.get(path)
        if handler:
            handler()
        else:
            self._send_404()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # 动态路由: /api/event/<id>/category
        m = re.match(r"^/api/event/(\d+)/category$", path)
        if m:
            self._api_event_category(int(m.group(1)))
            return

        routes = {
            "/api/capture": self._api_capture,
            "/api/pause": self._api_pause,
            "/api/toggle-auto": self._api_toggle_auto,
            "/api/report/daily": self._api_report_daily,
            "/api/report/weekly": self._api_report_weekly,
        }

        handler = routes.get(path)
        if handler:
            handler()
        else:
            self._send_404()

    def do_OPTIONS(self) -> None:
        """CORS 预检."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── 页面服务 ──────────────────────────────────────

    def _serve_dashboard(self) -> None:
        """返回仪表盘 HTML 页面."""
        app = self.app_ref
        hotkey = "Ctrl+Shift+P"
        if app:
            hotkey = app.config.get("screenshot", {}).get("hotkey", "ctrl+shift+p").upper()
        html = DASHBOARD_HTML.replace("%HOTKEY%", hotkey)
        self._send_html(html)

    def _serve_report_file(self, path: str) -> None:
        """提供报告文件."""
        if not self.app_ref:
            self._send_404()
            return
        # path 格式: /reports/daily/2026-06-30.md
        rel_path = path.lstrip("/")
        file_path = self.app_ref.project_root / rel_path
        if file_path.exists() and file_path.is_file():
            content = file_path.read_text(encoding="utf-8")
            self._send_html(f"<!DOCTYPE html><html><head><meta charset=utf-8>"
                           f"<title>{file_path.name}</title>"
                           f"<style>body{{font-family:monospace;max-width:800px;margin:40px auto;"
                           f"padding:20px;background:#1a1a2e;color:#e0e0e0;line-height:1.6;"
                           f"white-space:pre-wrap;}}</style></head><body>{content}</body></html>")
        else:
            self._send_404()

    # ── API 端点 ──────────────────────────────────────

    def _api_status(self) -> None:
        app = self.app_ref
        if not app:
            self._send_json({"error": "App not ready"}, 503)
            return
        today = date.today()
        self._send_json({
            "is_paused": app.screenshot_capture.is_paused,
            "is_auto": app.screenshot_capture.is_auto,
            "capture_mode": app.screenshot_capture.capture_mode,
            "auto_interval": app.config["screenshot"]["auto_interval_minutes"],
            "today_screenshots": app.store.get_screenshot_count_for_date(today),
            "today_events": len(app.store.get_today_events()),
            "privacy_skips": app.privacy.get_stats()["skip_count"],
            "privacy_blurs": app.privacy.get_stats()["blur_count"],
            "ocr_matches": app.privacy.get_stats()["ocr_match_count"],
            "has_daily_report": app.store.get_daily_report(today) is not None,
            "status_text": app._get_status_text(),
            "next_reports": {
                k: str(v) if v else None
                for k, v in app.scheduler.get_next_report_times().items()
            },
        })

    def _api_events_today(self) -> None:
        app = self.app_ref
        if not app:
            self._send_json({"error": "App not ready"}, 503)
            return
        events = app.store.get_today_events()
        self._send_json(events)

    def _api_events_today_llm(self) -> None:
        """返回今日事件，包含 raw_response 用于 LLM 诊断面板."""
        app = self.app_ref
        if not app:
            self._send_json({"error": "App not ready"}, 503)
            return
        events = app.store.get_today_events()
        # 只返回有 LLM 分析的事件，精简字段
        result = []
        for e in events:
            raw = (e.get("raw_response") or "").strip()
            result.append({
                "id": e["id"],
                "time": e.get("timestamp", "")[11:16] if e.get("timestamp") else "",
                "activity": e.get("activity", ""),
                "category": e.get("category", ""),
                "detail": e.get("detail", ""),
                "project": e.get("project", ""),
                "raw_response": raw,
                "has_llm": bool(raw),
            })
        self._send_json(result)

    def _api_events_recent(self) -> None:
        app = self.app_ref
        if not app:
            self._send_json({"error": "App not ready"}, 503)
            return
        events = app.store.get_recent_events(50)
        self._send_json(events)

    def _api_stats(self) -> None:
        app = self.app_ref
        if not app:
            self._send_json({"error": "App not ready"}, 503)
            return
        self._send_json({
            "privacy": app.privacy.get_stats(),
            "today_screenshots": app.store.get_screenshot_count_for_date(date.today()),
        })

    def _api_reports(self) -> None:
        app = self.app_ref
        if not app:
            self._send_json({"error": "App not ready"}, 503)
            return
        reports: list[dict] = []
        for rtype in ("daily", "weekly"):
            rdir = app.project_root / "reports" / rtype
            if rdir.exists():
                for f in sorted(rdir.glob("*.md"), reverse=True):
                    mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m/%d %H:%M")
                    reports.append({
                        "type": rtype,
                        "filename": f.name,
                        "path": f"reports/{rtype}/{f.name}",
                        "mtime": mtime,
                    })
        self._send_json(reports)

    def _api_capture(self) -> None:
        app = self.app_ref
        if not app:
            self._send_json({"error": "App not ready"}, 503)
            return
        try:
            results = app.screenshot_capture.capture_all_screens()
            for r in results:
                app._on_screenshot_captured(r)
            valid = [r for r in results if not r.skipped]
            self._send_json({
                "success": True,
                "message": f"截屏完成: {len(valid)}/{len(results)} 张",
                "total": len(results),
                "valid": len(valid),
            })
        except Exception as e:
            self._send_json({"success": False, "message": str(e)}, 500)

    def _api_toggle_auto(self) -> None:
        """切换自动截屏."""
        app = self.app_ref
        if not app:
            self._send_json({"error": "App not ready"}, 503)
            return
        app._toggle_auto()
        self._send_json({
            "success": True,
            "is_auto": app.screenshot_capture.is_auto,
            "message": "自动截屏已开启" if app.screenshot_capture.is_auto else "自动截屏已关闭",
        })

    def _api_pause(self) -> None:
        app = self.app_ref
        if not app:
            self._send_json({"error": "App not ready"}, 503)
            return
        app.screenshot_capture._paused = not app.screenshot_capture._paused
        app.tray.update_icon(app.screenshot_capture._paused)
        state = "已暂停" if app.screenshot_capture._paused else "已恢复"
        self._send_json({
            "success": True,
            "is_paused": app.screenshot_capture._paused,
            "message": f"截屏{state}",
        })

    ALLOWED_CATEGORIES = {
        "创作构建", "阅读查阅", "沟通协作", "分析计算",
        "会议讨论", "设计绘图", "学习研究", "娱乐休闲", "其他",
    }

    def _api_event_category(self, event_id: int) -> None:
        """更新事件的分类标签."""
        app = self.app_ref
        if not app:
            self._send_json({"error": "App not ready"}, 503)
            return
        try:
            body = json.loads(self._read_body())
            cat = (body.get("category") or "").strip()
            if cat not in self.ALLOWED_CATEGORIES:
                self._send_json({"error": f"无效分类: {cat}"}, 400)
                return
            ok = app.store.update_event_category(event_id, cat)
            if ok:
                self._send_json({"success": True})
            else:
                self._send_json({"error": "事件不存在"}, 404)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _api_report_today(self) -> None:
        """返回今日已有日报内容."""
        app = self.app_ref
        if not app:
            self._send_json({"error": "App not ready"}, 503)
            return
        today = date.today()
        report = app.store.get_daily_report(today)
        if report:
            self._send_json({"content": report.get("content", "")})
        else:
            self._send_json({"content": ""})

    def _api_report_daily(self) -> None:
        app = self.app_ref
        if not app:
            self._send_json({"error": "App not ready"}, 503)
            return
        try:
            content = app.scheduler.generate_daily_report_now()
            self._send_json({
                "success": True,
                "message": "日报生成完成",
                "content": content,
            })
        except Exception as e:
            self._send_json({"success": False, "message": str(e)}, 500)

    def _api_report_weekly(self) -> None:
        app = self.app_ref
        if not app:
            self._send_json({"error": "App not ready"}, 503)
            return
        try:
            content = app.scheduler.generate_weekly_report_now()
            self._send_json({
                "success": True,
                "message": "周报生成完成",
                "preview": content[:300] + "..." if len(content) > 300 else content,
            })
        except Exception as e:
            self._send_json({"success": False, "message": str(e)}, 500)


# ── Web 仪表盘服务 ───────────────────────────────────────

class WebDashboard:
    """本地 Web 仪表盘服务.

    Usage:
        dashboard = WebDashboard(app_ref, port=8765)
        dashboard.start()
    """

    def __init__(self, app_ref: Any, port: int = 8765):
        self.app_ref = app_ref
        self.port = port
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # 注入 app 引用到 handler
        DashboardHandler.app_ref = app_ref

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    def start(self) -> None:
        """启动 Web 服务（后台线程）."""
        if self._running:
            return

        self._server = HTTPServer(("127.0.0.1", self.port), DashboardHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="web-dashboard",
        )
        self._thread.start()
        self._running = True
        logger.info("📊 Web 仪表盘: %s", self.url)

    def stop(self) -> None:
        """停止 Web 服务."""
        if self._server:
            self._server.shutdown()
            self._running = False
            logger.info("Web 仪表盘已停止")
