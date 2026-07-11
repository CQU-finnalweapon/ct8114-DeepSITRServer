/**
 * ct8114 Frontend Enhancement Script
 * 
 * 1. 函数定位信息展板 — 读取后端输出的 functions 字段，按文件展示函数列表及定位
 * 2. Required/Advisory 级别标注 — 在缺陷列表中添加 Required(强制规则) / Advisory(推荐规则) 标注
 * 
 * 此脚本通过拦截 fetch 获取报告数据，Vue 渲染后在 DOM 中注入增强内容。
 */

(function () {
  'use strict';

  // ---------- state ----------
  let _lastReportData = null;
  let _injectedOnce = false;

  /** 拦截 fetch 以获得最新的报告 JSON 数据 */
  const _origFetch = window.fetch;
  window.fetch = function (...args) {
    return _origFetch.apply(this, args).then(function (resp) {
      const url = String(args[0] || '');
      if (/\/status\//.test(url) || /\/report\//.test(url) || /\/analyze/.test(url)) {
        resp.clone().text().then(function (text) {
          try {
            const data = JSON.parse(text);
            if (data && (data.report || data.summary || data.files_stats)) {
              _lastReportData = data;
              _injectedOnce = false;
              setTimeout(injectAll, 600);
            }
          } catch (_) { /* not JSON */ }
        });
      }
      return resp;
    });
  };

  function getReportData() {
    if (_lastReportData) return _lastReportData;
    return null;
  }

  /** Extract functions from the report payload */
  function extractFunctions(report) {
    const result = [];
    const r = (report && report.report) ? report.report : report;

    // from files_stats[].functions (per-file list)
    if (Array.isArray(r.files_stats)) {
      for (const fs of r.files_stats) {
        if (Array.isArray(fs.functions)) {
          for (const fn of fs.functions) {
            result.push({
              name: fn.name || '',
              file_path: fs.file_path || fn.file_path || '',
              start_line: Number(fn.start_line) || 0,
              start_column: Number(fn.start_column) || 0,
              end_line: Number(fn.end_line) || 0,
              end_column: Number(fn.end_column) || 0,
            });
          }
        }
      }
    }

    // fallback: from summary.functions (aggregated)
    if (!result.length && r.summary && Array.isArray(r.summary.functions)) {
      for (const fn of r.summary.functions) {
        result.push({
          name: fn.name || '',
          file_path: fn.file_path || '',
          start_line: Number(fn.start_line) || 0,
          start_column: Number(fn.start_column) || 0,
          end_line: Number(fn.end_line) || 0,
          end_column: Number(fn.end_column) || 0,
        });
      }
    }

    return result;
  }

  /** Extract bug-level force mapping for Required/Advisory labels */
  function extractBugForceMap(report) {
    const map = {};  // key: "ruleId-file-line-col" -> force value
    const r = (report && report.report) ? report.report : report;

    if (r.summary && Array.isArray(r.summary.bugs)) {
      for (const b of r.summary.bugs) {
        const key = [b.rule_id, b.file_path, b.line, b.column].join('|');
        map[key] = String(b.force || '0');
      }
    }

    if (Array.isArray(r.files_stats)) {
      for (const fs of r.files_stats) {
        if (Array.isArray(fs.bugs)) {
          for (const b of fs.bugs) {
            const key = [b.rule_id, b.file_path, b.line, b.column].join('|');
            if (!(key in map)) map[key] = String(b.force || '0');
          }
        }
      }
    }

    return map;
  }

  function groupByFile(functions) {
    const map = {};
    for (const fn of functions) {
      const key = fn.file_path || '(unknown)';
      if (!map[key]) map[key] = [];
      map[key].push(fn);
    }
    return map;
  }

  function shortPath(p) {
    const parts = (p || '').split(/[\\/]/).filter(Boolean);
    if (parts.length <= 2) return p;
    return '.../' + parts.slice(-2).join('/');
  }

  function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /** Build the function panel HTML */
  function buildFunctionsHTML(functions) {
    if (!functions.length) return '';

    const byFile = groupByFile(functions);
    const fileKeys = Object.keys(byFile).sort();

    let html = '<div class="ct8114-func-panel tool-card" style="margin-top:16px">';
    html += '<div class="card-head"><div><h2>函数列表 (Functions)</h2>';
    html += '<p>' + fileKeys.length + ' 个文件, ' + functions.length + ' 个函数</p></div></div>';
    html += '<div class="card-body">';

    for (const file of fileKeys) {
      const fns = byFile[file];
      html += '<details style="margin-bottom:10px">';
      html += '<summary style="font-weight:600;cursor:pointer;padding:4px 0">' +
        shortPath(file) + ' (' + fns.length + ' 个函数)</summary>';
      html += '<table style="width:100%;font-size:13px;margin-top:6px">';
      html += '<thead><tr>' +
        '<th style="text-align:left">函数名</th>' +
        '<th style="text-align:center;width:80px">起始行:列</th>' +
        '<th style="text-align:center;width:80px">结束行:列</th>' +
        '<th style="text-align:center;width:50px">行数</th>' +
        '</tr></thead><tbody>';

      for (const fn of fns) {
        const lines = (fn.end_line - fn.start_line + 1) || 0;
        html += '<tr>' +
          '<td style="font-family:monospace;font-size:12px">' + esc(fn.name) + '</td>' +
          '<td style="text-align:center;font-size:11px">' + fn.start_line + ':' + fn.start_column + '</td>' +
          '<td style="text-align:center;font-size:11px">' + fn.end_line + ':' + fn.end_column + '</td>' +
          '<td style="text-align:center;font-size:11px">' + lines + '</td>' +
          '</tr>';
      }

      html += '</tbody></table></details>';
    }

    html += '</div></div>';
    return html;
  }

  /** Inject functions panel after the files_stats table */
  function injectFunctionsPanel() {
    const data = getReportData();
    if (!data) return;

    const functions = extractFunctions(data);

    let root = document.getElementById('ct8114-func-root');
    if (!root) {
      root = document.createElement('div');
      root.id = 'ct8114-func-root';
      // find files_stats section
      const cards = document.querySelectorAll('.tool-card h2');
      let target = null;
      for (const h2 of cards) {
        if (/files_stats|文件统计/i.test(h2.textContent || '')) {
          target = h2.closest('.tool-card');
          break;
        }
      }
      if (target && target.nextSibling) {
        target.parentNode.insertBefore(root, target.nextSibling);
      } else if (target) {
        target.parentNode.appendChild(root);
      } else {
        const stack = document.querySelector('.result-stack');
        if (stack) stack.appendChild(root);
        else return;
      }
    }

    if (!functions.length) {
      root.innerHTML = '';
      root.style.display = 'none';
      return;
    }

    root.innerHTML = buildFunctionsHTML(functions);
    root.style.display = '';
  }

  /** Add Required/Advisory tooltip to level badges in the bug list table */
  function injectForceLabels() {
    const data = getReportData();
    if (!data) return;

    const forceMap = extractBugForceMap(data);
    if (!Object.keys(forceMap).length) return;

    // Find the bug table rows and annotate the level badge
    const bugTable = document.querySelector('.tool-card h2');
    let tableContainer = null;
    // find the 缺陷列表 section
    const allH2 = document.querySelectorAll('.tool-card h2');
    for (const h2 of allH2) {
      if (/缺陷列表/.test(h2.textContent || '')) {
        tableContainer = h2.closest('.tool-card');
        break;
      }
    }
    if (!tableContainer) return;

    const rows = tableContainer.querySelectorAll('tbody tr');
    rows.forEach(function (row) {
      if (row.hasAttribute('data-ct8114-force-labeled')) return;
      row.setAttribute('data-ct8114-force-labeled', '1');

      const cells = row.querySelectorAll('td');
      if (cells.length < 2) return;

      // cell[0] = level badge, cell[1] = rule_id
      const ruleCell = cells[1];
      const ruleId = (ruleCell.textContent || '').trim();

      // Find matching force value from the map
      let matchedForce = '0';
      for (const key of Object.keys(forceMap)) {
        if (key.indexOf(ruleId) >= 0) {
          matchedForce = forceMap[key];
          break;
        }
      }

      const levelCell = cells[0];
      const badge = levelCell.querySelector('.badge');
      if (badge) {
        const label = matchedForce === '1' ? 'Required (强制)' : 'Advisory (推荐)';
        badge.setAttribute('title', label + ' — ' + (badge.textContent || '').trim());
        badge.style.cursor = 'help';
        // Add a small indicator
        if (matchedForce === '1') {
          badge.textContent = badge.textContent.trim() + ' ¹';
        } else {
          badge.textContent = badge.textContent.trim() + ' ²';
        }
      }
    });

    // Add legend
    if (!document.getElementById('ct8114-force-legend')) {
      const legend = document.createElement('div');
      legend.id = 'ct8114-force-legend';
      legend.style.cssText = 'font-size:11px;color:#6b7280;margin-top:4px;padding:4px 8px;';
      legend.innerHTML = '<span>¹ Required(强制规则) — 可能有逻辑错误，一般要求改正</span>  ' +
        '<span>² Advisory(推荐规则) — 潜在问题，不强制修复</span>';
      tableContainer.querySelector('.card-body') && tableContainer.querySelector('.card-body').appendChild(legend);
    }
  }

  /** Run all injections */
  function injectAll() {
    if (_injectedOnce) return;
    injectFunctionsPanel();
    injectForceLabels();
    _injectedOnce = true;
  }

  /** Inject styles */
  function injectStyles() {
    if (document.getElementById('ct8114-enhance-styles')) return;
    const style = document.createElement('style');
    style.id = 'ct8114-enhance-styles';
    style.textContent = [
      '/* ct8114 增强样式 */',
      '.ct8114-func-panel table { border-collapse:collapse; }',
      '.ct8114-func-panel th { background:#f3f4f6; padding:6px 8px; border:1px solid #e5e7eb; font-size:12px; }',
      '.ct8114-func-panel td { padding:4px 8px; border:1px solid #e5e7eb; }',
      '.ct8114-func-panel tr:hover { background:#f9fafb; }',
      '.ct8114-func-panel details summary { color:#374151; font-size:14px; }',
      '.ct8114-func-panel details summary:hover { color:#1f2937; }',
    ].join('\n');
    document.head.appendChild(style);
  }

  /** Watch DOM for Vue renders */
  function watchDOM() {
    let debounceTimer = null;
    const observer = new MutationObserver(function () {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        _injectedOnce = false;
        injectAll();
      }, 500);
    });
    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(injectAll, 1500);
  }

  // ---------- init ----------
  injectStyles();
  watchDOM();
  console.log('[ct8114] 增强脚本已加载: 函数列表面板 + Required/Advisory 标注');
})();

  /** Inject functions panel after the files_stats table */
  function injectFunctionsPanel() {
    const data = getReportData();
    if (!data) return;

    const functions = extractFunctions(data);
    if (!functions.length) {
      // hide any previous panel
      const old = document.getElementById('ct8114-func-root');
      if (old) old.style.display = 'none';
      return;
    }

    const html = buildFunctionsHTML(functions);

    // find the files_stats section and insert after it
    const sections = document.querySelectorAll('.tool-card');
    let insertAfter = null;
    for (const sec of sections) {
      const h2 = sec.querySelector('h2');
      if (h2 && /files_stats|文件统计/i.test(h2.textContent || '')) {
        insertAfter = sec;
        break;
      }
    }

    // if not found, try to insert after the last tool-card in the result area
    if (!insertAfter) {
      const resultStack = document.querySelector('.result-stack');
      if (resultStack) {
        const cards = resultStack.querySelectorAll('.tool-card');
        if (cards.length) insertAfter = cards[cards.length - 1];
      }
    }

    let root = document.getElementById('ct8114-func-root');
    if (!root) {
      root = document.createElement('div');
      root.id = 'ct8114-func-root';
      if (insertAfter && insertAfter.nextSibling) {
        insertAfter.parentNode.insertBefore(root, insertAfter.nextSibling);
      } else if (insertAfter) {
        insertAfter.parentNode.appendChild(root);
      } else {
        // fallback: append after the result-stack
        const stack = document.querySelector('.result-stack');
        if (stack) stack.appendChild(root);
        else return;
      }
    }

    root.innerHTML = html;
    root.style.display = '';
  }

  /** Add Error/Warning badge styles - level mapping is correctly handled by backend */
  function injectLevelStyles() {
    if (document.getElementById('ct8114-level-styles')) return;
    const style = document.createElement('style');
    style.id = 'ct8114-level-styles';
    style.textContent = `
      /* ct8114 级别样式 - Required=Error(红色), Advisory=Warning(黄色) */
      .badge-red { background:#fee2e2; color:#991b1b; }
      .badge-yellow { background:#fef3c7; color:#92400e; }
      .badge-green { background:#dcfce7; color:#166534; }
      .badge-blue { background:#dbeafe; color:#1e40af; }

      /* 函数列表面板样式 */
      .ct8114-func-panel table { border-collapse:collapse; }
      .ct8114-func-panel th { background:#f3f4f6; padding:6px 8px; border:1px solid #e5e7eb; }
      .ct8114-func-panel td { padding:4px 8px; border:1px solid #e5e7eb; }
      .ct8114-func-panel tr:hover { background:#f9fafb; }
      .ct8114-func-panel details summary { color:#374151; }
    `;
    document.head.appendChild(style);
  }

  /** Watch for DOM changes — Vue renders, then we inject */
  function watchDOM() {
    const observer = new MutationObserver(function () {
      const cards = document.querySelectorAll('.tool-card h2');
      for (const h2 of cards) {
        if (/files_stats|文件统计|缺陷列表/.test(h2.textContent || '')) {
          // give Vue a moment to finish rendering
          setTimeout(injectFunctionsPanel, 300);
          break;
        }
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });

    // also try on initial load
    setTimeout(injectFunctionsPanel, 1500);
  }

  // ---------- init ----------
  injectLevelStyles();
  watchDOM();

  console.log('[ct8114] enhancement script loaded — functions panel + level badges ready');
})();
