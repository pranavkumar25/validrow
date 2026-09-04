/*
 * Client behaviour for the parts of the design that must feel instant.
 *
 * Navigation, filtering, sorting and paging are server-rendered links — they
 * are shareable URLs and they work without this file. What lives here is only
 * what a round-trip would ruin: pointer tracking on the chart, expanding a row,
 * selecting rows, and polling a running job.
 */
(function () {
  'use strict';

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  };

  // --- "/" focuses search, Escape closes any open popover ------------------
  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
      var box = $('#sb-q');
      if (box) { e.preventDefault(); box.focus(); box.select(); }
    }
    if (e.key === 'Escape') {
      $$('[data-sort-menu], [data-del-menu], [data-acct-menu]').forEach(function (m) {
        m.hidden = true;
      });
    }
  });

  // --- Dismissible banner --------------------------------------------------
  // Remembered per engine state so dismissing "SMTP is off" does not also
  // silence a later, different warning.
  var banner = $('[data-banner]');
  if (banner) {
    var key = 'vr-banner:' + (banner.textContent || '').slice(0, 60);
    if (sessionStorage.getItem(key) === '1') banner.hidden = true;
    var dismiss = $('[data-dismiss-banner]', banner);
    if (dismiss) {
      dismiss.addEventListener('click', function () {
        banner.hidden = true;
        sessionStorage.setItem(key, '1');
      });
    }
  }

  // --- Volume chart hover --------------------------------------------------
  var chart = $('#volchart');
  if (chart) {
    var points = [];
    try { points = JSON.parse(chart.getAttribute('data-points') || '[]'); } catch (err) { points = []; }
    var hover = $('[data-hover]', chart);
    var rule = $('[data-rule]', chart);
    var dot = $('[data-dot]', chart);
    var tip = $('[data-tip]', chart);
    var tipDate = $('[data-tip-date]', chart);
    var tipTotal = $('[data-tip-total]', chart);
    var tipRows = $('[data-tip-rows]', chart);
    var last = -1;

    var show = function (idx) {
      var p = points[idx];
      if (!p) return;
      var left = (idx / Math.max(1, points.length - 1)) * 100;
      // The SVG scales to the container; y is in viewBox units (248 tall).
      var top = (p.y / 248) * chart.clientHeight;
      hover.hidden = false;
      rule.style.left = left + '%';
      dot.style.left = left + '%';
      dot.style.top = top + 'px';
      tip.style.left = left + '%';
      tip.style.bottom = 'calc(100% - ' + top + 'px + 14px)';
      if (idx === last) return;
      last = idx;
      tipDate.textContent = p.label;
      tipTotal.textContent = p.total;
      tipRows.innerHTML = p.rows.map(function (r) {
        return '<div style="display: flex; align-items: center; gap: 8px">' +
          '<span style="width: 6px; height: 6px; border-radius: 999px; background: ' + r.dot + '"></span>' +
          '<span style="flex: 1; font-size: 12px; color: #D8D4CC">' + r.label + '</span>' +
          '<span style="font-size: 12px; font-weight: 500; color: #FFFFFF; font-variant-numeric: tabular-nums">' + r.value + '</span>' +
          '</div>';
      }).join('');
    };

    chart.addEventListener('mousemove', function (e) {
      if (!points.length) return;
      var r = chart.getBoundingClientRect();
      var n = points.length - 1;
      var idx = Math.max(0, Math.min(n, Math.round(((e.clientX - r.left) / r.width) * n)));
      show(idx);
    });
    chart.addEventListener('mouseleave', function () {
      hover.hidden = true;
      last = -1;
    });
  }

  // --- Sort dropdown -------------------------------------------------------
  $$('[data-sort]').forEach(function (wrap) {
    var toggle = $('[data-sort-toggle]', wrap);
    var menu = $('[data-sort-menu]', wrap);
    if (!toggle || !menu) return;
    toggle.addEventListener('click', function (e) {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
    });
    document.addEventListener('click', function (e) {
      if (!wrap.contains(e.target)) menu.hidden = true;
    });
  });

  // --- Expandable Contacts rows -------------------------------------------
  $$('[data-arow]').forEach(function (wrap) {
    var toggle = $('[data-row-toggle]', wrap);
    var detail = $('[data-row-detail]', wrap);
    if (!toggle || !detail) return;
    toggle.addEventListener('click', function (e) {
      // The checkbox is inside the row but owns its own click.
      if (e.target.closest('[data-check]')) return;
      if (!detail.hidden) { detail.hidden = true; return; }
      if (detail.dataset.loaded) { detail.hidden = false; return; }
      detail.innerHTML = '<div style="grid-column: 1 / -1; padding: 8px 0; font-size: 12.5px; color: #79756C">Loading trace…</div>';
      detail.hidden = false;
      fetch('/addresses/detail?email=' + encodeURIComponent(detail.dataset.email))
        .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
        .then(function (html) { detail.innerHTML = html; detail.dataset.loaded = '1'; })
        .catch(function () {
          detail.innerHTML = '<div style="grid-column: 1 / -1; padding: 8px 0; font-size: 12.5px; color: #B42318">Could not load the trace.</div>';
        });
    });
  });

  // --- Row selection + sticky action bar -----------------------------------
  var selected = new Set();
  var bar = $('[data-selbar]');

  var paint = function (el, on) {
    var box = el.matches('[data-check]') ? el : $('[data-box]', el);
    var row = el.matches('[data-check]') ? el.closest('[data-row-toggle]') : el;
    if (box) {
      box.style.background = on ? '#35ABFF' : '#FFFFFF';
      box.style.borderColor = on ? '#35ABFF' : '#D8D4CC';
      var tick = box.querySelector('svg');
      if (tick) tick.style.opacity = on ? '1' : '0';
    }
    if (row) {
      row.style.background = on ? '#EAF6FF' : 'transparent';
      row.setAttribute('aria-checked', on ? 'true' : 'false');
    }
  };

  var selectAll = $('[data-select-all]');
  var rowEls = $$('[data-select]');

  var sync = function () {
    if (bar) {
      bar.hidden = selected.size === 0;
      var label = $('[data-selcount]', bar);
      if (label) label.textContent = selected.size + ' selected';
    }
    if (selectAll) {
      var all = rowEls.length > 0 && selected.size === rowEls.length;
      paint(selectAll, all);
      // Distinguish "some" from "none" without a third icon.
      selectAll.style.borderColor = selected.size && !all ? '#35ABFF' : (all ? '#35ABFF' : '#D8D4CC');
    }
  };

  var toggleSel = function (el, email) {
    var on = !selected.has(email);
    if (on) selected.add(email); else selected.delete(email);
    paint(el, on);
    sync();
  };

  rowEls.forEach(function (el) {
    var email = el.getAttribute('data-select');
    el.addEventListener('click', function (e) {
      e.stopPropagation();
      toggleSel(el, email);
    });
    el.addEventListener('keydown', function (e) {
      if (e.key === ' ' || e.key === 'Enter') {
        e.preventDefault();
        toggleSel(el, email);
      }
    });
  });

  if (selectAll) {
    var toggleAll = function () {
      var turnOn = selected.size < rowEls.length;
      selected.clear();
      rowEls.forEach(function (el) {
        if (turnOn) selected.add(el.getAttribute('data-select'));
        paint(el, turnOn);
      });
      sync();
    };
    selectAll.addEventListener('click', toggleAll);
    selectAll.addEventListener('keydown', function (e) {
      if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); toggleAll(); }
    });
  }

  if (bar) {
    var clear = $('[data-clear-sel]', bar);
    if (clear) {
      clear.addEventListener('click', function () {
        rowEls.forEach(function (el) { paint(el, false); });
        selected.clear();
        sync();
      });
    }
    var exp = $('[data-export-selected]', bar);
    if (exp) {
      exp.addEventListener('click', function () {
        // Posted as a form so the browser handles the file download itself.
        var f = document.createElement('form');
        f.method = 'post';
        f.action = '/exports/download';
        selected.forEach(function (email) {
          var i = document.createElement('input');
          i.type = 'hidden';
          i.name = 'emails';
          i.value = email;
          f.appendChild(i);
        });
        document.body.appendChild(f);
        f.submit();
        document.body.removeChild(f);
      });
    }
  }

  // --- Raw response accordion ---------------------------------------------
  var rawToggle = $('[data-raw-toggle]');
  var raw = $('[data-raw]');
  if (rawToggle && raw) {
    rawToggle.addEventListener('click', function () { raw.hidden = !raw.hidden; });
  }

  // --- Delete-job popover, gated on typing the filename --------------------
  var delMenu = $('[data-del-menu]');
  if (delMenu) {
    $$('[data-del-toggle]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        e.stopPropagation();
        delMenu.hidden = !delMenu.hidden;
      });
    });
    var input = $('[data-del-input]', delMenu);
    var submit = $('[data-del-submit]', delMenu);
    if (input && submit) {
      input.addEventListener('input', function () {
        var ok = input.value.trim() === input.dataset.expect;
        submit.disabled = !ok;
        submit.style.opacity = ok ? '1' : '.5';
      });
    }
  }

  // --- Account menu --------------------------------------------------------
  var acct = $('[data-acct]');
  if (acct) {
    var acctToggle = $('[data-acct-toggle]', acct);
    var acctMenu = $('[data-acct-menu]', acct);
    acctToggle.addEventListener('click', function (e) {
      e.stopPropagation();
      acctMenu.hidden = !acctMenu.hidden;
      acctToggle.setAttribute('aria-expanded', acctMenu.hidden ? 'false' : 'true');
    });
    document.addEventListener('click', function (e) {
      if (!acct.contains(e.target)) {
        acctMenu.hidden = true;
        acctToggle.setAttribute('aria-expanded', 'false');
      }
    });
  }

  // --- Copy buttons --------------------------------------------------------
  // Either an explicit `data-copy-value`, or the text of a nearby `data-copy-src`.
  $$('[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var value = btn.getAttribute('data-copy-value');
      if (!value) {
        var src = $('[data-copy-src]');
        value = src ? src.textContent.trim() : '';
      }
      if (!value || !navigator.clipboard) return;
      navigator.clipboard.writeText(value).then(function () {
        var was = btn.textContent;
        btn.textContent = 'Copied';
        setTimeout(function () { btn.textContent = was; }, 1400);
      });
    });
  });

  // --- Upload: browse, drag and drop ---------------------------------------
  var zone = $('#drop-zone');
  var fileInput = $('#file-input');
  var uploadForm = $('#upload-form');
  if (zone && fileInput && uploadForm) {
    var submitFile = function () {
      $('#drop-label').textContent = 'Reading ' + fileInput.files[0].name + '…';
      zone.disabled = true;
      uploadForm.submit();
    };
    zone.addEventListener('click', function () { fileInput.click(); });
    fileInput.addEventListener('change', function () {
      if (fileInput.files.length) submitFile();
    });
    ['dragenter', 'dragover'].forEach(function (ev) {
      zone.addEventListener(ev, function (e) {
        e.preventDefault();
        zone.style.borderColor = '#35ABFF';
        zone.style.background = '#EAF6FF';
      });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      zone.addEventListener(ev, function (e) {
        e.preventDefault();
        zone.style.borderColor = '#D8D4CC';
        zone.style.background = '#FFFFFF';
      });
    });
    zone.addEventListener('drop', function (e) {
      if (!e.dataTransfer.files.length) return;
      fileInput.files = e.dataTransfer.files;
      submitFile();
    });
  }

  // --- Live job progress ---------------------------------------------------
  // Patched in place, never reloaded, so the bar animates instead of flashing.
  // Only a terminal status swaps the page for the finished view.
  var poll = $('[data-poll]');
  if (poll) {
    var jobId = poll.getAttribute('data-poll');
    var VERDICTS = ['deliverable', 'risky', 'unknown', 'undeliverable'];
    var LABELS = { deliverable: 'valid', risky: 'risky', unknown: 'unknown', undeliverable: 'invalid' };
    var nf = new Intl.NumberFormat('en-US');

    var phraseFor = function (job) {
      if (job.phase === 'reading') return 'Reading and de-duplicating rows';
      if (job.phase === 'resolving') return 'Resolving ' + nf.format(job.domains_total) + ' domains';
      if (job.phase === 'verifying') return 'Verifying ' + nf.format(job.processed) + ' of ' + nf.format(job.total);
      if (job.phase === 'assembling') return 'Finalizing';
      return 'Queued';
    };

    var fold = function (counts) {
      return {
        deliverable: counts.valid || 0,
        risky: counts.risky || 0,
        unknown: counts.unknown || 0,
        undeliverable: (counts.invalid || 0) + (counts.disposable || 0) + (counts.spam_trap || 0)
      };
    };

    var paintJob = function (job) {
      var pct = (job.progress * 100).toFixed(1) + '%';
      var bar = $('[data-bar]', poll);
      if (bar) bar.style.width = pct;
      var pctEl = $('[data-pct]', poll);
      if (pctEl) pctEl.textContent = pct;
      var phase = $('[data-phase]', poll);
      if (phase) phase.textContent = phraseFor(job);
      if (job.duration != null) {
        var el = $('[data-elapsed]', poll);
        var s = Math.round(job.duration);
        if (el) el.textContent = Math.floor(s / 60) + 'm ' + String(s % 60).padStart(2, '0') + 's';
      }
      var t = fold(job.counts || {});
      var sum = VERDICTS.reduce(function (a, k) { return a + t[k]; }, 0);
      VERDICTS.forEach(function (k) {
        var card = poll.querySelector('[data-card="' + k + '"]');
        if (!card) return;
        $('[data-count]', card).textContent = nf.format(t[k]);
        $('[data-pctlabel]', card).textContent = sum ? Math.round((t[k] / sum) * 100) + '%' : '—';
      });
    };

    var tick = function () {
      fetch('/v1/jobs/' + jobId)
        .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
        .then(function (job) {
          if (job.status === 'completed' || job.status === 'failed') {
            location.href = '/validate?job=' + jobId;
            return;
          }
          paintJob(job);
          setTimeout(tick, 1000);
        })
        .catch(function () { setTimeout(tick, 4000); });
    };
    setTimeout(tick, 1000);
  }
})();
