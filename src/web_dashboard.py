"""Web 仪表盘 — 本地 HTTP 服务，提供可视化工作状态面板和 API

启动后在浏览器打开 http://localhost:8765 查看仪表盘.
"""

import json
import logging
import threading
from datetime import date, datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

# ── HTML 仪表盘页面 ──────────────────────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Work Reporter — 仪表盘</title>
<style>
  :root {
    --bg: #1a1a2e; --card: #16213e; --accent: #4A90D9; --accent2: #7B68EE;
    --text: #e0e0e0; --text2: #a0a0a0; --success: #2ecc71; --warn: #f39c12;
    --danger: #e74c3c; --border: #2a2a4a;
    --cat-code: #4A90D9; --cat-doc: #2ecc71; --cat-comm: #f39c12;
    --cat-browse: #9b59b6; --cat-meeting: #e74c3c; --cat-design: #1abc9c;
    --cat-learn: #7B68EE; --cat-misc: #8899aa; --cat-other: #95a5a6;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:'Segoe UI','Microsoft YaHei',sans-serif; background:var(--bg); color:var(--text); min-height:100vh; }
  .header { background:linear-gradient(135deg,var(--card),#0f3460); padding:24px 32px; border-bottom:1px solid var(--border); }
  .header h1 { font-size:22px; font-weight:600; }
  .header .subtitle { color:var(--text2); margin-top:4px; font-size:13px; }
  .container { max-width:1200px; margin:0 auto; padding:24px; }
  .stats-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:14px; margin-bottom:24px; }
  .stat-card { background:var(--card); border-radius:12px; padding:18px; border:1px solid var(--border); transition:transform 0.2s; }
  .stat-card:hover { transform:translateY(-2px); }
  .stat-card .label { color:var(--text2); font-size:11px; text-transform:uppercase; letter-spacing:1px; }
  .stat-card .value { font-size:30px; font-weight:700; margin:6px 0; }
  .stat-card .value.accent { color:var(--accent); }
  .stat-card .value.success { color:var(--success); }
  .stat-card .value.warn { color:var(--warn); }
  .actions { display:flex; gap:10px; margin-bottom:24px; flex-wrap:wrap; }
  .btn { padding:10px 18px; border-radius:8px; border:none; cursor:pointer; font-size:13px; font-weight:500; transition:all 0.2s; display:flex; align-items:center; gap:5px; }
  .btn:hover { transform:translateY(-1px); box-shadow:0 4px 12px rgba(0,0,0,0.3); }
  .btn-primary { background:var(--accent); color:white; }
  .btn-secondary { background:var(--border); color:var(--text); }
  .btn-success { background:var(--success); color:white; }
  .btn-warn { background:var(--warn); color:white; }
  .layout-2col { display:grid; grid-template-columns:1fr 1fr; gap:24px; margin-bottom:24px; }
  @media (max-width:800px) { .layout-2col { grid-template-columns:1fr; } }
  .section { background:var(--card); border-radius:12px; padding:20px; border:1px solid var(--border); }
  .section h2 { font-size:15px; font-weight:600; margin-bottom:14px; display:flex; align-items:center; gap:8px; }
  .section h3 { font-size:13px; font-weight:600; color:var(--accent); padding:8px 0 4px; border-bottom:1px solid var(--border); margin-bottom:8px; display:flex; align-items:center; gap:6px; }
  .timeline { max-height:520px; overflow-y:auto; }
  .event-item { display:flex; align-items:flex-start; gap:10px; padding:8px 12px; font-size:12px; border-left:3px solid transparent; margin:2px 0; border-radius:0 6px 6px 0; transition:background 0.15s; }
  .event-item:hover { background:rgba(255,255,255,0.03); }
  .event-item.cat-code { border-left-color:var(--cat-code); }
  .event-item.cat-doc { border-left-color:var(--cat-doc); }
  .event-item.cat-comm { border-left-color:var(--cat-comm); }
  .event-item.cat-browse { border-left-color:var(--cat-browse); }
  .event-item.cat-meeting { border-left-color:var(--cat-meeting); }
  .event-item.cat-design { border-left-color:var(--cat-design); }
  .event-item.cat-learn { border-left-color:var(--cat-learn); }
  .event-item.cat-misc { border-left-color:var(--cat-misc); }
  .event-item.cat-other { border-left-color:var(--cat-other); }
  .event-time { color:var(--accent); font-family:monospace; min-width:48px; font-size:11px; }
  .event-gap { color:var(--text2); font-size:10px; min-width:48px; text-align:center; }
  .event-body { flex:1; min-width:0; }
  .event-title { font-weight:500; word-break:break-word; font-size:13px; }
  .event-desc { color:var(--text2); font-size:11px; margin-top:3px; line-height:1.5; word-break:break-word; }
  .event-footer { display:flex; gap:6px; align-items:center; margin-top:4px; flex-wrap:wrap; }
  .cat-select { font-size:10px; padding:2px 4px; border-radius:4px; background:var(--bg); color:var(--text2); border:1px solid var(--border); cursor:pointer; }
  .cat-select:focus { outline:1px solid var(--accent); }
  .badge { display:inline-block; padding:1px 7px; border-radius:4px; font-size:10px; font-weight:500; }
  .badge-code { background:rgba(74,144,217,0.2); color:#7db8f0; }
  .badge-doc { background:rgba(46,204,113,0.2); color:#5ddb8e; }
  .badge-comm { background:rgba(243,156,18,0.2); color:#f7b84e; }
  .badge-browse { background:rgba(155,89,182,0.2); color:#b07cd8; }
  .badge-meeting { background:rgba(231,76,60,0.2); color:#e8837a; }
  .badge-design { background:rgba(26,188,156,0.2); color:#48d1b5; }
  .badge-learn { background:rgba(123,104,238,0.2); color:#a99df4; }
  .badge-misc { background:rgba(136,153,170,0.2); color:#a0b0c0; }
  .badge-other { background:rgba(160,160,160,0.2); color:#bbb; }
  .project-tag { color:var(--accent2); font-size:10px; background:rgba(123,104,238,0.15); padding:1px 6px; border-radius:3px; }
  .heatmap-container { margin-bottom:12px; }
  .heatmap-legend { display:flex; gap:10px; margin-bottom:8px; font-size:10px; color:var(--text2); flex-wrap:wrap; align-items:center; }
  .heatmap-legend .legend-item { display:flex; align-items:center; gap:4px; }
  .heatmap-legend .legend-swatch { width:12px; height:12px; border-radius:2px; flex-shrink:0; }
  .heatmap-bars { display:flex; gap:4px; align-items:flex-end; }
  .heatmap-bar-col { display:flex; flex-direction:column; align-items:center; }
  .heatmap-bar { width:18px; height:120px; display:flex; flex-direction:column-reverse; border-radius:2px; overflow:hidden; outline:1px solid rgba(255,255,255,0.06); flex-shrink:0; }
  .heatmap-seg { width:100%; flex-shrink:0; transition:opacity 0.2s; }
  .heatmap-seg:hover { opacity:0.7; }
  .heatmap-label { font-size:9px; color:var(--text2); margin-top:3px; font-family:monospace; }
  .empty { text-align:center; color:var(--text2); padding:40px; font-size:14px; }
  .status-dot { width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:8px; }
  .status-dot.active { background:var(--success); animation:pulse 2s infinite; }
  .status-dot.paused { background:var(--warn); }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.5; } }
  .toast { position:fixed; top:20px; right:20px; background:var(--card); border:1px solid var(--border); padding:12px 20px; border-radius:8px; font-size:13px; z-index:1000; animation:slideIn 0.3s ease; }
  @keyframes slideIn { from { transform:translateX(100%); opacity:0; } to { transform:translateX(0); opacity:1; } }
  ::-webkit-scrollbar { width:5px; } ::-webkit-scrollbar-track { background:transparent; } ::-webkit-scrollbar-thumb { background:var(--border); border-radius:3px; }
  .tab-bar { display:flex; gap:0; margin-bottom:14px; border-bottom:2px solid var(--border); }
  .tab-btn { padding:8px 16px; font-size:13px; font-weight:500; cursor:pointer; border:none; background:none; color:var(--text2); border-bottom:2px solid transparent; margin-bottom:-2px; transition:all 0.2s; }
  .tab-btn:hover { color:var(--text); }
  .tab-btn.active { color:var(--accent); border-bottom-color:var(--accent); }
  .llm-event { background:rgba(255,255,255,0.02); border:1px solid var(--border); border-radius:8px; padding:14px; margin-bottom:10px; }
  .llm-event-header { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
  .llm-event-header .time { color:var(--accent); font-family:monospace; font-size:12px; }
  .llm-event-header .activity { font-weight:600; font-size:13px; flex:1; }
  .llm-event-header .badge { flex-shrink:0; }
  .llm-raw { font-family:'Cascadia Code','Fira Code',monospace; font-size:11px; line-height:1.5; background:rgba(0,0,0,0.3); border-radius:6px; padding:12px; white-space:pre-wrap; word-break:break-word; max-height:300px; overflow-y:auto; color:#c0c0c0; }
  .llm-no-data { text-align:center; color:var(--text2); padding:20px; font-size:13px; }
  .llm-meta { display:flex; gap:10px; font-size:10px; color:var(--text2); margin-bottom:8px; flex-wrap:wrap; }
</style>
</head>
<body>
<div class="header">
  <h1>📊 Work Reporter 仪表盘</h1>
  <div class="subtitle" id="statusText">加载中...</div>
</div>
<div class="container">
  <div class="stats-grid">
    <div class="stat-card"><div class="label">📸 今日截图</div><div class="value accent" id="ssCount">-</div></div>
    <div class="stat-card"><div class="label">📝 今日事件</div><div class="value accent" id="evtCount">-</div></div>
    <div class="stat-card"><div class="label">🛡 隐私过滤</div><div class="value success" id="privSkip">-</div></div>
    <div class="stat-card"><div class="label">⏯ 截屏状态</div><div class="value warn" id="pauseStatus">-</div></div>
    <div class="stat-card"><div class="label">📋 日报</div><div class="value" id="dailyStatus" style="font-size:16px;">-</div></div>
  </div>

  <div class="actions">
    <button class="btn btn-primary" onclick="apiPost('/api/capture')">📸 截屏</button>
    <button class="btn btn-warn" onclick="apiPost('/api/pause')">⏯ 暂停</button>
    <button class="btn btn-success" onclick="apiPost('/api/report/daily')">📄 日报</button>
    <button class="btn btn-secondary" onclick="apiPost('/api/report/weekly')">📊 周报</button>
    <button class="btn btn-secondary" onclick="window.open('/reports')">📁 报告</button>
  </div>

  <div class="section" style="margin-bottom:24px;">
    <div class="tab-bar">
      <button class="tab-btn active" onclick="switchTab('timeline')" id="tab-timeline">🕐 活动时间线</button>
      <button class="tab-btn" onclick="switchTab('llm')" id="tab-llm">🤖 LLM 原始输出</button>
    </div>
    <div class="heatmap-container" id="heatmapBar"></div>
    <div class="timeline" id="eventList"><div class="empty">暂无今日事件，按 <kbd>%HOTKEY%</kbd> 开始截屏</div></div>
    <div id="llmPanel" style="display:none;"><div class="llm-no-data">加载中...</div></div>
  </div>

  <div class="section">
    <h2>📋 最近报告</h2>
    <div id="recentReports" style="font-size:13px;"><div class="empty">暂无报告</div></div>
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
    $('dailyStatus').textContent = status.has_daily_report ? '✅ 已生成' : '⏳ 未生成';

    var events = await fetchJSON(API+'/events/today');
    renderHeatmap(events);
    if (events.length > 0) {
      $('eventList').innerHTML = renderTimeline(events);
    } else {
      $('eventList').innerHTML = '<div class="empty">暂无今日事件，按快捷键开始截屏 📸</div>';
    }

    var reports = await fetchJSON(API+'/reports');
    if (reports.length > 0) {
      $('recentReports').innerHTML = reports.slice(0,5).map(function(r) {
        return '<div style="padding:6px 0;border-bottom:1px solid var(--border);">'+
          '<a href="/reports/'+r.type+'/'+r.filename+'" style="color:var(--accent);text-decoration:none;">📄 '+r.filename+'</a>'+
          '<span style="color:var(--text2);font-size:11px;margin-left:12px;">'+ (r.mtime||'') +'</span></div>';
      }).join('');
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
        import re
        m = re.match(r"^/api/event/(\d+)/category$", path)
        if m:
            self._api_event_category(int(m.group(1)))
            return

        routes = {
            "/api/capture": self._api_capture,
            "/api/pause": self._api_pause,
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
                "preview": content[:300] + "..." if len(content) > 300 else content,
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
