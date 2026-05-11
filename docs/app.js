/**
 * app.js – Hauptlogik des Thüringer Haushaltsplan Dashboards.
 * Lädt haushalt.db via DuckDB-WASM, füllt alle UI-Bereiche.
 */

import * as duckdb from "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm";

const DB_URL = "./data/haushalt.db";   // Relativer Pfad (GitHub Pages)

let db = null;
let conn = null;

// ── DuckDB initialisieren ─────────────────────────────────────────────────────
async function initDuckDB() {
  const JSDELIVR_BUNDLES = duckdb.getJsDelivrBundles();
  const bundle = await duckdb.selectBundle(JSDELIVR_BUNDLES);
  const worker_url = URL.createObjectURL(
    new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" })
  );
  const worker = new Worker(worker_url);
  const logger = new duckdb.ConsoleLogger();
  db = new duckdb.AsyncDuckDB(logger, worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  conn = await db.connect();

  // SQLite-DB laden (über HTTP fetch → DuckDB SQLite-Extension)
  await conn.query(`INSTALL sqlite; LOAD sqlite;`);
  const resp = await fetch(DB_URL);
  if (!resp.ok) throw new Error(`Konnte haushalt.db nicht laden (${resp.status})`);
  const buf = await resp.arrayBuffer();
  await db.registerFileBuffer("haushalt.db", new Uint8Array(buf));
  await conn.query(`ATTACH 'haushalt.db' AS haus (TYPE SQLITE);`);
}

// ── SQL-Abfrage-Wrapper ───────────────────────────────────────────────────────
async function query(sql) {
  const result = await conn.query(sql);
  return result.toArray().map(r => r.toJSON());
}

// ── Zahlen formatieren ────────────────────────────────────────────────────────
function formatEUR(v, decimals = 1) {
  if (v == null) return "–";
  const abs = Math.abs(v);
  if (abs >= 1e9)       return `${(v / 1e9).toFixed(decimals)} Mrd. €`;
  if (abs >= 1e6)       return `${(v / 1e6).toFixed(decimals)} Mio. €`;
  if (abs >= 1e3)       return `${(v / 1e3).toFixed(0)} Tsd. €`;
  return `${v.toFixed(0)} €`;
}
function formatEURTable(v) {
  if (v == null) return "–";
  return new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(v);
}

// ── Statusbar ─────────────────────────────────────────────────────────────────
function setStatus(msg, type = "info") {
  const bar = document.getElementById("status-bar");
  bar.textContent = msg;
  bar.className = `status-bar ${type}`;
  bar.classList.remove("hidden");
}
function clearStatus() {
  document.getElementById("status-bar").classList.add("hidden");
}

// ── Kacheln befüllen ──────────────────────────────────────────────────────────
async function fillKacheln() {
  const grid = document.getElementById("kacheln-grid");
  const rows = await query(`
    SELECT
      SUM(ansatz_2026)                                       AS gesamt,
      SUM(CASE WHEN hauptgruppe='4' THEN ansatz_2026 END)    AS personal,
      SUM(CASE WHEN hauptgruppe='8' THEN ansatz_2026 END)    AS invest,
      COUNT(DISTINCT kapitel)                                AS kapitel_n
    FROM haus.haushaltsstellen
  `);
  const r = rows[0];
  grid.innerHTML = `
    <div class="kachel">
      <div class="k-label">Gesamthaushalt 2026</div>
      <div class="k-value">${formatEUR(r.gesamt, 1)}</div>
      <div class="k-sub">Alle Einzelpläne</div>
    </div>
    <div class="kachel">
      <div class="k-label">Personalausgaben 2026</div>
      <div class="k-value">${formatEUR(r.personal, 1)}</div>
      <div class="k-sub">${r.gesamt ? ((r.personal / r.gesamt) * 100).toFixed(1) : '–'} % des Haushalts</div>
    </div>
    <div class="kachel">
      <div class="k-label">Investitionen 2026</div>
      <div class="k-value">${formatEUR(r.invest, 1)}</div>
      <div class="k-sub">Hauptgruppe 8</div>
    </div>
    <div class="kachel">
      <div class="k-label">Haushaltsstellen</div>
      <div class="k-value">${r.kapitel_n}</div>
      <div class="k-sub">Kapitel gesamt</div>
    </div>
  `;
}

// ── Dropdown Ministerien ──────────────────────────────────────────────────────
async function fillDropdowns() {
  const eps = await query(`
    SELECT nr, name FROM haus.einzelplaene ORDER BY nr
  `);
  const sel = document.getElementById("f-ep");
  eps.forEach(ep => {
    const opt = document.createElement("option");
    opt.value = ep.nr;
    opt.textContent = `EP ${ep.nr} – ${ep.name}`;
    sel.appendChild(opt);
  });
}

// ── Treemap ───────────────────────────────────────────────────────────────────
async function renderTreemap() {
  const container = document.getElementById("treemap-container");
  const rows = await query(`
    SELECT ministerium, SUM(ansatz_2026) AS summe
    FROM haus.haushaltsstellen
    GROUP BY ministerium
    ORDER BY summe DESC
  `);

  const total = rows.reduce((s, r) => s + (r.summe || 0), 0);
  const W = container.clientWidth - 24;
  const H = Math.max(380, W * 0.45);
  container.style.height = H + "px";

  const COLORS = [
    "#2e7d32","#388e3c","#43a047","#4caf50","#66bb6a",
    "#1b5e20","#558b2f","#33691e","#827717","#f57f17",
    "#e65100","#bf360c","#4e342e","#37474f","#263238",
  ];

  // Einfache Treemap-Implementierung (Squarified)
  function squarify(items, rect) {
    if (!items.length) return [];
    const result = [];
    let remaining = [...items];
    let { x, y, w, h } = rect;
    while (remaining.length) {
      const isHorizontal = w >= h;
      let row = [];
      let rowSum = 0;
      let best = Infinity;
      for (let i = 0; i < remaining.length; i++) {
        row.push(remaining[i]);
        rowSum += remaining[i].val;
        const rowLen = isHorizontal ? w * rowSum / total_local : h * rowSum / total_local;
        const ratio = row.reduce((worst, item) => {
          const side = isHorizontal ? h * item.val / rowSum : w * item.val / rowSum;
          const r = Math.max(rowLen / side, side / rowLen);
          return Math.max(worst, r);
        }, 0);
        if (ratio > best && row.length > 1) { row.pop(); rowSum -= remaining[i].val; break; }
        best = ratio;
      }
      const rowLen = isHorizontal ? w * rowSum / total_local : h * rowSum / total_local;
      let cur = isHorizontal ? y : x;
      row.forEach(item => {
        const side = (isHorizontal ? h : w) * item.val / rowSum;
        result.push(isHorizontal
          ? { ...item, x, y: cur, w: rowLen, h: side }
          : { ...item, x: cur, y, w: side, h: rowLen }
        );
        cur += side;
      });
      if (isHorizontal) { x += rowLen; w -= rowLen; }
      else              { y += rowLen; h -= rowLen; }
      remaining = remaining.slice(row.length);
    }
    return result;
  }

  const total_local = total;
  const items = rows.map((r, i) => ({
    name: r.ministerium, val: r.summe || 0, color: COLORS[i % COLORS.length],
  }));
  const cells = squarify(items, { x: 0, y: 0, w: W, h: H });

  container.innerHTML = "";
  cells.forEach(c => {
    const div = document.createElement("div");
    div.className = "treemap-cell";
    div.style.cssText = `
      left:${c.x}px; top:${c.y}px; width:${c.w}px; height:${c.h}px;
      background:${c.color};
    `;
    div.innerHTML = `
      <div class="cell-name">${c.name.split(" ").slice(-2).join(" ")}</div>
      <div class="cell-value">${formatEUR(c.val)}</div>
    `;
    div.title = `${c.name}: ${formatEURTable(c.val)}`;
    div.addEventListener("click", () => {
      document.getElementById("f-ep").value = "";
      // Ministerium-Name in Suche setzen
      document.getElementById("f-search").value = "";
      runExplorer({ ministerium: c.name });
    });
    container.appendChild(div);
  });
}

// ── Explorer ──────────────────────────────────────────────────────────────────
async function runExplorer(overrides = {}) {
  const ep     = overrides.ep      ?? document.getElementById("f-ep").value;
  const hgr    = overrides.hgr     ?? document.getElementById("f-hgr").value;
  const jahr   = document.getElementById("f-jahr").value;
  const search = overrides.search  ?? document.getElementById("f-search").value.trim();

  const conditions = [];
  if (ep)     conditions.push(`e.einzelplan = '${ep.replace(/'/g, "''")}'`);
  if (hgr)    conditions.push(`e.hauptgruppe = '${hgr}'`);
  if (search) conditions.push(`(e.titel_name LIKE '%${search.replace(/'/g, "''")}%' OR e.kapitel_name LIKE '%${search.replace(/'/g, "''")}%')`);

  const where = conditions.length ? "WHERE " + conditions.join(" AND ") : "";
  const sql = `
    SELECT e.ministerium, e.kapitel, e.kapitel_name,
           e.titel, e.titel_name, e.hauptgruppe_name,
           e.${jahr} AS betrag
    FROM haus.haushaltsstellen e
    ${where}
    ORDER BY betrag DESC NULLS LAST
    LIMIT 200
  `;

  const rows = await query(sql);
  renderTable(rows, document.getElementById("explorer-result"), {
    columns: [
      { key: "ministerium",    label: "Ministerium" },
      { key: "kapitel",        label: "Kap." },
      { key: "titel",          label: "Titel" },
      { key: "titel_name",     label: "Bezeichnung" },
      { key: "hauptgruppe_name", label: "Art" },
      { key: "betrag",         label: "Betrag", num: true, format: formatEURTable },
    ],
    sumCol: "betrag",
    emptyText: "Keine Treffer – bitte Filter anpassen."
  });
}

// ── Tabelle rendern ───────────────────────────────────────────────────────────
function renderTable(rows, container, { columns, sumCol, emptyText = "Keine Daten." }) {
  if (!rows.length) {
    container.innerHTML = `<p style="padding:1.2rem;color:var(--gray-400)">${emptyText}</p>`;
    return;
  }
  const sum = sumCol ? rows.reduce((s, r) => s + (r[sumCol] ?? 0), 0) : null;
  const thead = columns.map(c => `<th>${c.label}</th>`).join("");
  const tbody = rows.map(r =>
    `<tr>${columns.map(c => {
      const v = r[c.key];
      const formatted = c.format ? c.format(v) : (v ?? "–");
      return `<td class="${c.num ? 'num' : ''}">${formatted}</td>`;
    }).join("")}</tr>`
  ).join("");
  const tfoot = sumCol ? `
    <tfoot>
      <tr class="tfoot-row">
        ${columns.map((c, i) => i === 0
          ? `<td>Summe (${rows.length} Stellen)</td>`
          : c.key === sumCol
            ? `<td class="num">${formatEURTable(sum)}</td>`
            : `<td></td>`
        ).join("")}
      </tr>
    </tfoot>` : "";
  container.innerHTML = `
    <table><thead><tr>${thead}</tr></thead>
    <tbody>${tbody}</tbody>${tfoot}</table>
  `;
}

// ── Meta-Badge ────────────────────────────────────────────────────────────────
async function fillMeta() {
  try {
    const resp = await fetch("./data/meta.json");
    const meta = await resp.json();
    document.getElementById("meta-badge").innerHTML =
      `Stand: ${meta.stand} · ${(meta.summe_2026_eur / 1e9).toFixed(1)} Mrd. € (2026)`;
    document.getElementById("footer-stand").textContent =
      `Daten: ${meta.stand} · Quelle: ${meta.quelle}`;
  } catch (_) { /* meta.json noch nicht vorhanden */ }
}

// ── Bootstrapping ─────────────────────────────────────────────────────────────
async function boot() {
  setStatus("⏳ Lade Datenbankverbindung …", "info");
  try {
    await initDuckDB();
    clearStatus();
  } catch (e) {
    // DB noch nicht vorhanden → Demo-Modus mit Hinweis
    setStatus(
      `⚠️ haushalt.db noch nicht gefunden. Bitte zuerst die Pipeline ausführen: python pipeline/01_download.py --pilot && python pipeline/02_parse.py --pilot && python pipeline/03_build_db.py`,
      "error"
    );
    document.getElementById("kacheln-grid").innerHTML =
      `<div class="kachel"><div class="k-label">Hinweis</div><div class="k-sub">Datenbank noch nicht generiert. Führe die Pipeline aus.</div></div>`;
    fillMeta();
    initUI();   // UI trotzdem aufbauen
    return;
  }

  await Promise.all([fillMeta(), fillKacheln(), fillDropdowns()]);
  await renderTreemap();
  initUI();
}

function initUI() {
  // NL-Suche
  document.getElementById("nl-btn").addEventListener("click", handleNLQuery);
  document.getElementById("nl-input").addEventListener("keydown", e => {
    if (e.key === "Enter") handleNLQuery();
  });

  // Quick-Buttons
  document.querySelectorAll(".quick-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.getElementById("nl-input").value = btn.dataset.q;
      handleNLQuery();
    });
  });

  // Explorer
  document.getElementById("filter-btn").addEventListener("click", () => runExplorer());

  // Antwort schließen
  document.getElementById("answer-close").addEventListener("click", () => {
    document.getElementById("answer-section").classList.add("hidden");
  });

  // Modal
  document.getElementById("key-save").addEventListener("click", () => {
    const url = document.getElementById("worker-url-input").value.trim();
    if (url) { localStorage.setItem("worker_url", url); }
    document.getElementById("key-modal").classList.add("hidden");
    handleNLQuery();
  });
  document.getElementById("key-cancel").addEventListener("click", () => {
    document.getElementById("key-modal").classList.add("hidden");
  });
}

// ── NL-Query ──────────────────────────────────────────────────────────────────
async function handleNLQuery() {
  const question = document.getElementById("nl-input").value.trim();
  if (!question) return;

  const workerUrl = localStorage.getItem("worker_url");
  if (!workerUrl) {
    document.getElementById("key-modal").classList.remove("hidden");
    return;
  }

  const btn = document.getElementById("nl-btn");
  btn.disabled = true;
  btn.textContent = "…";
  setStatus("🤔 KI generiert SQL-Abfrage …", "info");

  try {
    const resp = await fetch(workerUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!resp.ok) throw new Error(`Worker-Fehler: ${resp.status}`);
    const data = await resp.json();

    if (data.error) throw new Error(data.error);

    const sql = data.sql;
    const rows = await query(sql);

    clearStatus();
    const section = document.getElementById("answer-section");
    section.classList.remove("hidden");
    document.getElementById("answer-title").textContent = `💬 ${question}`;
    document.getElementById("answer-sql").textContent = sql;

    // Spalten automatisch ermitteln
    const cols = rows.length
      ? Object.keys(rows[0]).map(k => ({
          key: k, label: k.replace(/_/g, " "),
          num: typeof rows[0][k] === "number",
          format: typeof rows[0][k] === "number" ? formatEURTable : undefined,
        }))
      : [];
    const numCols = cols.filter(c => c.num);
    renderTable(rows, document.getElementById("table-container"), {
      columns: cols,
      sumCol: numCols.length === 1 ? numCols[0].key : null,
    });

    // Einfaches Balkendiagramm wenn sinnvoll
    renderBarChart(rows, numCols[0]?.key);

    section.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    setStatus(`✗ ${e.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Fragen →";
  }
}

// ── Balkendiagramm (nativ, kein Framework) ────────────────────────────────────
function renderBarChart(rows, numKey) {
  const container = document.getElementById("chart-container");
  if (!numKey || rows.length < 2 || rows.length > 40) {
    container.innerHTML = "";
    return;
  }
  const labelKey = Object.keys(rows[0]).find(k => typeof rows[0][k] === "string");
  if (!labelKey) { container.innerHTML = ""; return; }

  const maxVal = Math.max(...rows.map(r => r[numKey] ?? 0));
  const bars = rows.slice(0, 20).map(r => {
    const pct = maxVal > 0 ? ((r[numKey] ?? 0) / maxVal * 100).toFixed(1) : 0;
    const label = String(r[labelKey] ?? "").substring(0, 45);
    return `
      <div style="display:flex;align-items:center;gap:.6rem;margin:.25rem 0;">
        <div style="width:200px;font-size:.75rem;color:var(--gray-600);text-align:right;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${label}">${label}</div>
        <div style="flex:1;background:var(--gray-100);border-radius:3px;height:20px;position:relative;">
          <div style="width:${pct}%;background:var(--green);height:100%;border-radius:3px;transition:width .4s;"></div>
        </div>
        <div style="width:120px;font-size:.75rem;color:var(--gray-800);text-align:right;font-variant-numeric:tabular-nums;">${formatEURTable(r[numKey])}</div>
      </div>
    `;
  }).join("");
  container.innerHTML = `<div style="padding:.5rem 0;">${bars}</div>`;
}

boot();
