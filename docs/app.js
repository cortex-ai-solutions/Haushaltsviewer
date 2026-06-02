/**
 * app.js – Thüringer Haushaltsplan Dashboard
 * DuckDB-WASM lädt haushalt.db direkt im Browser – kein Server nötig.
 */

import * as duckdb from "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm";

const DB_URL = "./data/haushalt.db";

// Kurznamen für Treemap-Beschriftung (EP-Nummer → kompakter Name)
const EP_KURZ = {
  "01": "Landtag",
  "02": "Staatskanzlei",
  "03": "Inneres",
  "04": "Bildung",
  "05": "Justiz",
  "06": "Finanzen",
  "07": "Wirtschaft",
  "08": "Soziales",
  "09": "Umwelt",
  "10": "Digitales",
  "11": "Rechnungshof",
  "12": "Verfassungsgericht",
  "16": "IuK",
  "17": "Finanzverwaltung",
  "18": "Hochbau",
};

// ── Schuldendaten aus §2 ThürHhG + Kreditfinanzierungsplan (Seite 25) ─────────
// Quelle: Thüringer Haushaltsgesetz 2026/2027
const SCHULDEN_REF = {
  netto_2026:          866_832_200,   // §2(1) ThürHhG
  netto_2027:          551_493_900,   // §2(1) ThürHhG
  brutto_aufnahme_2026: 1_776_800_000, // Teil III Gesamtplan
  tilgung_2026:          910_000_000,  // §2(2) ThürHhG
  brutto_aufnahme_2027: 1_275_600_000, // Teil III Gesamtplan
  tilgung_2027:          724_100_000,  // §2(2) ThürHhG
  kassenkredit_limit_pct: 12,          // §2(4) = 12% des festgestellten Betrags
};

let db   = null;
let conn = null;

// ── DuckDB initialisieren ─────────────────────────────────────────────────────
async function initDuckDB() {
  const bundles   = duckdb.getJsDelivrBundles();
  const bundle    = await duckdb.selectBundle(bundles);
  const workerUrl = URL.createObjectURL(
    new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" })
  );
  const worker = new Worker(workerUrl);
  db   = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  conn = await db.connect();

  // Schema anlegen
  await conn.query(`CREATE SCHEMA IF NOT EXISTS haus;`);

  // Tabellen aus JSON laden (kein SQLite-Extension nötig – DuckDB WASM native)
  const TABLES = [
    "haushaltsstellen", "kapitel", "einzelplaene",
    "stellenplan", "stellenuebersicht", "gesamtplan_referenz",
  ];

  let geladen = 0;
  for (const t of TABLES) {
    try {
      const resp = await fetch(`./data/${t}.json`);
      if (!resp.ok) continue;
      const text = await resp.text();
      await db.registerFileText(`${t}.json`, text);
      await conn.query(
        `CREATE TABLE IF NOT EXISTS haus.${t} AS
         SELECT * FROM read_json_auto('${t}.json')`
      );
      geladen++;
    } catch (_) { /* optionale Tabelle fehlt */ }
  }

  if (geladen === 0) throw new Error("Keine Datentabellen gefunden – JSON-Dateien fehlen.");

  // Views
  await conn.query(`
    CREATE VIEW IF NOT EXISTS haus.v_personal AS
    SELECT * FROM haus.haushaltsstellen WHERE hauptgruppe = '4';
  `);
  await conn.query(`
    CREATE VIEW IF NOT EXISTS haus.v_ministerium_summen AS
    SELECT ministerium, einzelplan,
      SUM(CASE WHEN hauptgruppe='4' THEN ansatz_2026 ELSE 0 END) AS personal_2026,
      SUM(CASE WHEN hauptgruppe='4' THEN ansatz_2027 ELSE 0 END) AS personal_2027,
      SUM(CASE WHEN hauptgruppe IN ('4','5','6','7','8','9') THEN ansatz_2026 ELSE 0 END) AS ausgaben_2026,
      SUM(CASE WHEN hauptgruppe IN ('4','5','6','7','8','9') THEN ansatz_2027 ELSE 0 END) AS ausgaben_2027,
      SUM(ansatz_2026) AS gesamt_2026,
      SUM(ansatz_2027) AS gesamt_2027
    FROM haus.haushaltsstellen
    GROUP BY ministerium, einzelplan;
  `);
}

// ── Abfrage-Wrapper ───────────────────────────────────────────────────────────
async function query(sql) {
  const result = await conn.query(sql);
  return result.toArray().map(r => r.toJSON());
}

// ── Zahlenformate ─────────────────────────────────────────────────────────────
function fmtEUR(v, dec = 1) {
  if (v == null) return "–";
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(dec)} Mrd. €`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(dec)} Mio. €`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(0)} Tsd. €`;
  return `${v.toFixed(0)} €`;
}
function fmtEURFull(v) {
  if (v == null) return "–";
  return new Intl.NumberFormat("de-DE", {
    style: "currency", currency: "EUR", maximumFractionDigits: 0,
  }).format(v);
}
function fmtN(v) {
  if (v == null || v === 0) return "0";
  return new Intl.NumberFormat("de-DE").format(v);
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

// ── Kacheln befüllen ─────────────────────────────────────────────────────────
let _kachelnJahr = "2026";

async function fillKacheln(jahr = _kachelnJahr) {
  _kachelnJahr = jahr;
  const grid = document.getElementById("kacheln-grid");
  const title = document.getElementById("kacheln-title");
  if (title) title.textContent = `Gesamtübersicht ${jahr}`;
  // Jahr-Toggle aktiv markieren
  document.querySelectorAll("#kacheln-year-toggle .year-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.year === jahr)
  );

  const col = `ansatz_${jahr}`;
  const [gpRow, hRow, sRow, zinsenRow] = await Promise.all([
    query(`SELECT SUM(ausgaben_${jahr}) AS gesamt_ref FROM haus.gesamtplan_referenz`)
      .catch(() => [{ gesamt_ref: null }]),
    query(`
      SELECT
        SUM(CASE WHEN hauptgruppe='4' THEN ${col} END)          AS personal,
        SUM(CASE WHEN hauptgruppe IN ('7','8') THEN ${col} END)  AS invest,
        COUNT(DISTINCT kapitel)                                    AS kapitel_n
      FROM haus.haushaltsstellen
    `),
    query(`SELECT SUM(stellen) AS total FROM haus.stellenuebersicht
           WHERE jahr=${jahr} AND typ='Gesamt' AND kapitel != 'GESAMT'`)
      .catch(() => [{ total: null }]),
    query(`SELECT SUM(${col}) AS zinsen FROM haus.haushaltsstellen
           WHERE einzelplan='17' AND titel LIKE '575%'`)
      .catch(() => [{ zinsen: null }]),
  ]);

  const gesamtRef   = gpRow[0]?.gesamt_ref ?? null;
  const h           = hRow[0];
  const planstellen = sRow[0]?.total ?? null;
  const zinsen      = zinsenRow[0]?.zinsen ?? null;
  const personalPct = gesamtRef ? ((h.personal / gesamtRef) * 100).toFixed(1) : "–";

  grid.innerHTML = `
    <div class="kachel">
      <div class="k-icon">🏦</div>
      <div class="k-label">Gesamthaushalt 2026</div>
      <div class="k-value">${fmtEUR(gesamtRef, 1)}</div>
      <div class="k-sub">Ausgaben · §1 ThürHhG</div>
    </div>
    <div class="kachel kachel-personal">
      <div class="k-icon">👥</div>
      <div class="k-label">Personalausgaben 2026</div>
      <div class="k-value">${fmtEUR(h.personal, 1)}</div>
      <div class="k-sub">${personalPct} % des Haushalts</div>
    </div>
    <div class="kachel kachel-stellen">
      <div class="k-icon">🏢</div>
      <div class="k-label">Planstellen 2026</div>
      <div class="k-value">${planstellen != null ? fmtN(planstellen) : "–"}</div>
      <div class="k-sub">Beamte + Tarifbeschäftigte</div>
    </div>
    <div class="kachel kachel-invest">
      <div class="k-icon">📈</div>
      <div class="k-label">Investitionen 2026</div>
      <div class="k-value">${fmtEUR(h.invest, 1)}</div>
      <div class="k-sub">Hauptgruppen 7 + 8</div>
    </div>
    <div class="kachel">
      <div class="k-icon">📋</div>
      <div class="k-label">Kapitel gesamt</div>
      <div class="k-value">${h.kapitel_n}</div>
      <div class="k-sub">Haushaltskapitel</div>
    </div>
    <div class="kachel kachel-schulden" id="k-verschuldung" role="button" tabindex="0"
         title="Details zu Schulden &amp; Zinsen anzeigen" style="cursor:pointer">
      <div class="k-icon">📉</div>
      <div class="k-label">Neuverschuldung 2026</div>
      <div class="k-value">${fmtEUR(SCHULDEN_REF.netto_2026, 1)}</div>
      <div class="k-sub">Zinslast: ${zinsen ? fmtEUR(zinsen, 0) : "–"} · Details →</div>
    </div>
  `;

  // Kachel-Klick direkt nach dem Rendern setzen (robuster als Event-Delegation in initUI)
  const kvEl = document.getElementById("k-verschuldung");
  if (kvEl) {
    kvEl.addEventListener("click", openSchuldenModal);
    kvEl.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") openSchuldenModal(); });
  }
}

// ── Dropdowns befüllen ────────────────────────────────────────────────────────
async function fillDropdowns() {
  const eps = await query(`SELECT nr, name FROM haus.einzelplaene ORDER BY nr`);

  // Haushalt-Tab: Ministeriums-Dropdown
  eps.forEach(ep => {
    el("f-ep")?.appendChild(new Option(`EP ${ep.nr} – ${ep.name}`, ep.nr));
  });

  // Stellen-Tab: gleiches Dropdown mit EP-Ministerien
  eps.forEach(ep => {
    el("s-ep")?.appendChild(new Option(`EP ${ep.nr} – ${ep.name}`, ep.nr));
  });
}

// ── Treemap ───────────────────────────────────────────────────────────────────
let _treemapJahr = "2026";

async function renderTreemap(jahr = _treemapJahr) {
  _treemapJahr = jahr;
  const container = document.getElementById("treemap-container");

  // Treemap-eigene Jahr-Buttons aktualisieren
  document.querySelectorAll("#struktur-year-toggle .year-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.year === jahr)
  );

  const col = `ansatz_${jahr}`;
  // Nur Ausgaben (HGr 4-9), 0-Werte filtern damit Squarify stabil bleibt
  const rows = await query(`
    SELECT einzelplan, ministerium, SUM(${col}) AS summe
    FROM haus.haushaltsstellen
    WHERE hauptgruppe IN ('4','5','6','7','8','9')
    GROUP BY einzelplan, ministerium
    HAVING summe > 0
    ORDER BY summe DESC
  `);

  const total = rows.reduce((s, r) => s + (r.summe || 0), 0);
  const W = container.clientWidth - 16;
  const H = Math.max(320, W * 0.42);
  container.style.height = H + "px";

  const COLORS = [
    "#1b5e20","#2e7d32","#388e3c","#43a047","#558b2f",
    "#33691e","#827717","#f57f17","#e65100","#bf360c",
    "#4e342e","#37474f","#263238","#1a237e","#311b92",
  ];

  function squarify(items, rect) {
    if (!items.length) return [];
    const result = [];
    let remaining = [...items];
    let { x, y, w, h } = rect;
    while (remaining.length) {
      const horiz = w >= h;
      let row = [], rowSum = 0, best = Infinity;
      for (let i = 0; i < remaining.length; i++) {
        row.push(remaining[i]);
        rowSum += remaining[i].val;
        const len = horiz ? w * rowSum / total : h * rowSum / total;
        const ratio = row.reduce((wst, it) => {
          const side = (horiz ? h : w) * it.val / rowSum;
          return Math.max(wst, Math.max(len / side, side / len));
        }, 0);
        if (ratio > best && row.length > 1) { row.pop(); rowSum -= remaining[i].val; break; }
        best = ratio;
      }
      const len = horiz ? w * rowSum / total : h * rowSum / total;
      let cur = horiz ? y : x;
      row.forEach(it => {
        const side = (horiz ? h : w) * it.val / rowSum;
        result.push(horiz
          ? { ...it, x, y: cur, w: len, h: side }
          : { ...it, x: cur, y, w: side, h: len });
        cur += side;
      });
      if (horiz) { x += len; w -= len; } else { y += len; h -= len; }
      remaining = remaining.slice(row.length);
    }
    return result;
  }

  // Nur positive Werte, mindestens 1 EUR (Schutz gegen Squarify-Division-by-Zero)
  const items = rows
    .filter(r => (r.summe || 0) > 0)
    .map((r, i) => ({
      name: r.ministerium,
      ep:   r.einzelplan,
      val:  r.summe,
      color: COLORS[i % COLORS.length],
    }));

  if (!items.length) {
    container.innerHTML = `<p style="color:var(--gray-400);padding:1rem">Keine Daten.</p>`;
    return;
  }

  const cells = squarify(items, { x: 0, y: 0, w: W, h: H });

  container.innerHTML = "";
  cells.forEach(c => {
    // Mindestgröße für Darstellung: 8×8 px
    if (c.w < 8 || c.h < 8) return;

    const div = document.createElement("div");
    div.className = "treemap-cell";
    // Math.round verhindert Sub-Pixel-Risse zwischen Zellen
    div.style.cssText =
      `left:${Math.round(c.x)}px;top:${Math.round(c.y)}px;` +
      `width:${Math.round(c.w)}px;height:${Math.round(c.h)}px;` +
      `background:${c.color}`;

    // Kurzname aus EP_KURZ-Tabelle, Fallback auf letztes Wort des vollen Namens
    const label = EP_KURZ[c.ep] || c.name.split(" ").pop();

    // Text nur anzeigen wenn Zelle groß genug
    const showText = c.w > 60 && c.h > 32;
    div.innerHTML = showText
      ? `<div class="cell-name">${label}</div><div class="cell-value">${fmtEUR(c.val)}</div>`
      : "";
    div.title = `${c.name}: ${fmtEURFull(c.val)}`;

    div.addEventListener("click", () => renderEpDetail(c.ep, c.name, jahr));
    container.appendChild(div);
  });
}

// ── EP-Detail unterhalb Treemap ───────────────────────────────────────────────
async function renderEpDetail(ep, name, jahr) {
  const detail = document.getElementById("treemap-ep-detail");
  const titleEl = document.getElementById("ep-detail-title");
  const tableEl = document.getElementById("ep-detail-table");
  if (!detail) return;

  detail.classList.remove("hidden");
  titleEl.textContent = `EP ${ep} – ${name} · Ausgaben ${jahr}`;
  tableEl.innerHTML = `<p style="padding:.8rem;color:var(--gray-400)">Lade …</p>`;
  detail.scrollIntoView({ behavior: "smooth", block: "nearest" });

  const col = `ansatz_${jahr}`;
  const rows = await query(`
    SELECT hauptgruppe_name, titel, titel_name, ${col} AS betrag
    FROM haus.haushaltsstellen
    WHERE einzelplan = '${esc(ep)}'
      AND hauptgruppe IN ('4','5','6','7','8','9')
      AND ${col} > 0
    ORDER BY betrag DESC
    LIMIT 100
  `).catch(() => []);

  if (!rows.length) {
    tableEl.innerHTML = `<p style="padding:.8rem;color:var(--gray-400)">Keine Ausgaben-Daten für EP ${ep}.</p>`;
    return;
  }

  const total = rows.reduce((s, r) => s + (r.betrag || 0), 0);
  const tbody = rows.map(r => `
    <tr>
      <td class="small-text">${r.hauptgruppe_name || "–"}</td>
      <td><span class="kap-badge" style="font-size:.72rem">${r.titel || "–"}</span></td>
      <td>${r.titel_name || "–"}</td>
      <td class="num">${fmtEURFull(r.betrag)}</td>
    </tr>`).join("");

  tableEl.innerHTML = `
    <table class="ep-detail-tbl">
      <thead><tr>
        <th>Ausgabenart</th><th>Titel</th><th>Bezeichnung</th><th class="num">Betrag ${jahr}</th>
      </tr></thead>
      <tbody>${tbody}</tbody>
      <tfoot><tr class="tfoot-row">
        <td colspan="3">Top ${rows.length} Ausgabentitel</td>
        <td class="num">${fmtEURFull(total)}</td>
      </tr></tfoot>
    </table>
    <p class="gp-hinweis">Nur Positionen > 0 EUR · HGr. 4–9 · max. 100 Einträge</p>
  `;
}

// ── Stellenplan-Balkendiagramm ────────────────────────────────────────────────
// Verwendet gesamtplan_referenz statt stellenuebersicht, da stellenuebersicht
// für viele EPs (z.B. EP04 Bildung) falsche/unvollständige Werte enthält.
// Die Gesamtplan-Referenz (Seite 134-139) hat die offiziellen Planstellen.
async function renderStellenBarChart() {
  const container = document.getElementById("stellen-bar-container");

  const rows = await query(`
    SELECT einzelplan, bezeichnung,
           COALESCE(beamte_soll_2026, 0) AS beamte,
           COALESCE(an_soll_2026, 0)     AS tarif,
           COALESCE(gesamt_soll_2026, 0) AS gesamt
    FROM haus.gesamtplan_referenz
    WHERE COALESCE(gesamt_soll_2026, 0) > 0
    ORDER BY gesamt DESC
  `).catch(() => []);

  if (!rows.length) {
    container.innerHTML = `<p style="color:var(--gray-400);padding:1rem">Keine Planstellen-Daten vorhanden.</p>`;
    return;
  }

  const maxVal = Math.max(...rows.map(r => r.gesamt));

  let html = `
    <div class="stellen-legend">
      <span class="leg-dot" style="background:#1b5e20"></span> Beamte
      <span class="leg-dot" style="background:#4caf50;margin-left:1rem"></span> Tarifbeschäftigte
    </div>
    <p class="chart-subtitle" style="margin:.4rem 0 .8rem">
      Quelle: §1 ThürHhG Stellenübersicht Gesamtplan · Klick auf Balken → Detailansicht
    </p>
    <div class="stellen-bars">
  `;

  rows.forEach(r => {
    const pctB   = maxVal > 0 ? (r.beamte / maxVal * 100).toFixed(1) : 0;
    const pctT   = maxVal > 0 ? (r.tarif  / maxVal * 100).toFixed(1) : 0;
    const epKurz = EP_KURZ[r.einzelplan] || `EP${r.einzelplan}`;

    html += `
      <div class="stellen-row" data-ep="${r.einzelplan}">
        <div class="stellen-label" title="${r.bezeichnung || epKurz}">
          <span class="kap-nr">${epKurz}</span>
          <span class="kap-name">${(r.bezeichnung || epKurz).substring(0, 38)}</span>
        </div>
        <div class="stellen-bar-wrap">
          <div class="stellen-bar-bg">
            <div class="stellen-bar beamte" style="width:${pctB}%"
                 title="${fmtN(r.beamte)} Beamte"></div>
            <div class="stellen-bar tarif"  style="width:${pctT}%;margin-top:2px"
                 title="${fmtN(r.tarif)} Tarifbeschäftigte"></div>
          </div>
        </div>
        <div class="stellen-total">${fmtN(r.gesamt)}</div>
      </div>
    `;
  });

  html += `</div>`;
  container.innerHTML = html;

  // Klick → Stellen-Detail nach EP filtern (data-ep aus gesamtplan_referenz)
  container.querySelectorAll(".stellen-row[data-ep]").forEach(row => {
    row.addEventListener("click", async () => {
      const ep = row.dataset.ep || "";
      if (el("s-ep"))          el("s-ep").value = ep;
      if (el("s-typ"))         el("s-typ").value = "";
      if (el("s-besoldung"))   el("s-besoldung").value = "";
      if (el("s-bezeichnung")) el("s-bezeichnung").value = "";
      try { await runStellenExplorer(); } catch (_) {}
      el("stellen-result")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  });
}

// ── Verschuldungs-Modal ───────────────────────────────────────────────────────
let _schuldenRendered = false;

async function renderVerschuldungDetails() {
  if (_schuldenRendered) return;
  _schuldenRendered = true;

  // Zinsaufwendungen aus DB laden
  const zinsenRows = await query(`
    SELECT titel, titel_name, ansatz_2026, ansatz_2027
    FROM haus.haushaltsstellen
    WHERE einzelplan='17' AND titel LIKE '575%'
    ORDER BY ansatz_2026 DESC
  `).catch(() => []);

  const zinsen26 = zinsenRows.reduce((s, r) => s + (r.ansatz_2026 || 0), 0);
  const zinsen27 = zinsenRows.reduce((s, r) => s + (r.ansatz_2027 || 0), 0);

  // ── Kennzahlen-Kacheln ────────────────────────────────────────────────────
  document.getElementById("schulden-kacheln").innerHTML = `
    <div class="schulden-kachel">
      <div class="sk-label">Nettoneuverschuldung 2026</div>
      <div class="sk-value sk-rot">${fmtEURFull(SCHULDEN_REF.netto_2026)}</div>
      <div class="sk-sub">§2(1) ThürHhG 2026/2027</div>
    </div>
    <div class="schulden-kachel">
      <div class="sk-label">Nettoneuverschuldung 2027</div>
      <div class="sk-value sk-rot">${fmtEURFull(SCHULDEN_REF.netto_2027)}</div>
      <div class="sk-sub">§2(1) ThürHhG 2026/2027</div>
    </div>
    <div class="schulden-kachel">
      <div class="sk-label">Zinsaufwendungen 2026</div>
      <div class="sk-value">${fmtEURFull(zinsen26)}</div>
      <div class="sk-sub">EP 17 Kap. 1706 · Titel 575xx</div>
    </div>
    <div class="schulden-kachel">
      <div class="sk-label">Zinsaufwendungen 2027</div>
      <div class="sk-value">${fmtEURFull(zinsen27)}</div>
      <div class="sk-sub">EP 17 Kap. 1706 · Titel 575xx</div>
    </div>
  `;

  // ── Kreditfinanzierungsplan-Chart ─────────────────────────────────────────
  const kreditData = [
    { label: "Brutto-Aufnahme 2026", wert: SCHULDEN_REF.brutto_aufnahme_2026, typ: "aufnahme" },
    { label: "Tilgung 2026",         wert: SCHULDEN_REF.tilgung_2026,         typ: "tilgung"  },
    { label: "Netto 2026",           wert: SCHULDEN_REF.netto_2026,           typ: "netto"    },
    { label: "Brutto-Aufnahme 2027", wert: SCHULDEN_REF.brutto_aufnahme_2027, typ: "aufnahme" },
    { label: "Tilgung 2027",         wert: SCHULDEN_REF.tilgung_2027,         typ: "tilgung"  },
    { label: "Netto 2027",           wert: SCHULDEN_REF.netto_2027,           typ: "netto"    },
  ];
  const maxK = Math.max(...kreditData.map(d => d.wert));
  const kreditColors = { aufnahme: "#c62828", tilgung: "#2e7d32", netto: "#e65100" };

  document.getElementById("schulden-kredit-chart").innerHTML = `
    <div style="padding:.5rem 0">
      ${kreditData.map(d => `
        <div style="display:flex;align-items:center;gap:.8rem;margin:.35rem 0;">
          <div style="width:180px;font-size:.8rem;color:var(--gray-600);text-align:right;flex-shrink:0">${d.label}</div>
          <div style="flex:1;background:var(--gray-100);border-radius:3px;height:22px;overflow:hidden;">
            <div style="width:${(d.wert/maxK*100).toFixed(1)}%;background:${kreditColors[d.typ]};height:100%;border-radius:3px"></div>
          </div>
          <div style="width:140px;font-size:.8rem;font-variant-numeric:tabular-nums;color:var(--gray-800);text-align:right">${fmtEURFull(d.wert)}</div>
        </div>`).join("")}
      <p style="font-size:.72rem;color:var(--gray-400);margin-top:.5rem">
        Quelle: §2 ThürHhG + Teil III Gesamtplan (Kreditfinanzierungsplan) · Beträge gerundet
      </p>
    </div>
  `;

  // ── Zinsen-Tabelle ────────────────────────────────────────────────────────
  if (zinsenRows.length) {
    const tbody = zinsenRows.map(r => `
      <tr>
        <td>${r.titel}</td>
        <td>${r.titel_name}</td>
        <td class="num">${fmtEURFull(r.ansatz_2026)}</td>
        <td class="num">${fmtEURFull(r.ansatz_2027)}</td>
      </tr>`).join("");

    document.getElementById("schulden-zinsen-table").innerHTML = `
      <table style="width:100%;font-size:.83rem">
        <thead><tr>
          <th style="text-align:left;padding:.4rem .6rem;background:var(--gray-100)">Titel</th>
          <th style="text-align:left;padding:.4rem .6rem;background:var(--gray-100)">Bezeichnung</th>
          <th style="text-align:right;padding:.4rem .6rem;background:var(--gray-100)">Ansatz 2026</th>
          <th style="text-align:right;padding:.4rem .6rem;background:var(--gray-100)">Ansatz 2027</th>
        </tr></thead>
        <tbody>${tbody}</tbody>
        <tfoot><tr style="font-weight:700;background:var(--gray-100)">
          <td colspan="2" style="padding:.4rem .6rem">Gesamt Zinsaufwendungen</td>
          <td class="num" style="padding:.4rem .6rem">${fmtEURFull(zinsen26)}</td>
          <td class="num" style="padding:.4rem .6rem">${fmtEURFull(zinsen27)}</td>
        </tr></tfoot>
      </table>
    `;
  }
}

// ── Tab-Switching ─────────────────────────────────────────────────────────────
let _stellenTabLoaded  = false;
let _strukturTabLoaded = true;   // Struktur ist Standard-Tab, wird sofort in boot() geladen

function switchTab(name) {
  document.querySelectorAll(".tab").forEach(t =>
    t.classList.toggle("active", t.dataset.tab === name)
  );
  ["haushalt", "stellen", "struktur", "gesamtplan"].forEach(t => {
    el(`tab-${t}`)?.classList.toggle("hidden", name !== t);
  });

  if (name === "gesamtplan") renderGesamtplanTab();

  // Stellen: Balkendiagramm + Explorer beim ersten Öffnen laden
  if (name === "stellen" && !_stellenTabLoaded) {
    _stellenTabLoaded = true;
    renderStellenBarChart().catch(() => {});
    runStellenExplorer().catch(() => {});
  }

  // Struktur: Treemap beim ersten Öffnen rendern
  if (name === "struktur" && !_strukturTabLoaded) {
    _strukturTabLoaded = true;
    renderTreemap().catch(() => {});
  }

  document.querySelector(".explorer-section").scrollIntoView({ behavior: "smooth" });
}

// ── Gesamtplan-Referenz-Tab ───────────────────────────────────────────────────
let _gesamtplanRendered = false;

async function renderGesamtplanTab() {
  if (_gesamtplanRendered) return;
  _gesamtplanRendered = true;

  const container = document.getElementById("gesamtplan-result");
  container.innerHTML = `<p style="padding:1rem;color:var(--gray-400)">Lade Referenzdaten …</p>`;

  // Gesamtplan-Referenz + EP-Parser-Summen parallel holen
  const [gpRows, epSumRows] = await Promise.all([
    query(`
      SELECT einzelplan, bezeichnung,
             einnahmen_2026, ausgaben_2026, personal_aus_2026,
             einnahmen_2027, ausgaben_2027
      FROM haus.gesamtplan_referenz
      ORDER BY einzelplan
    `).catch(() => []),
    query(`
      SELECT einzelplan,
             SUM(CASE WHEN hauptgruppe IN ('4','5','6','7','8','9') THEN ansatz_2026 ELSE 0 END) AS ep_aus26,
             SUM(CASE WHEN hauptgruppe IN ('4','5','6','7','8','9') THEN ansatz_2027 ELSE 0 END) AS ep_aus27,
             SUM(CASE WHEN hauptgruppe='4' THEN ansatz_2026 ELSE 0 END)  AS ep_pers26,
             COUNT(*) AS n_stellen
      FROM haus.haushaltsstellen
      GROUP BY einzelplan
    `).catch(() => []),
  ]);

  if (!gpRows.length) {
    container.innerHTML = `
      <p style="padding:1.5rem;color:var(--gray-400)">
        Gesamtplan-Referenz nicht verfügbar. Bitte zuerst
        <code>python pipeline/02c_parse_gesamtplan.py</code> ausführen.
      </p>`;
    return;
  }

  // EP-Parser-Summen als Map
  const epMap = {};
  epSumRows.forEach(r => { epMap[r.einzelplan] = r; });

  // Gesamtsummen
  const totalGP26   = gpRows.reduce((s, r) => s + (r.ausgaben_2026 || 0), 0);
  const totalGP27   = gpRows.reduce((s, r) => s + (r.ausgaben_2027 || 0), 0);
  const totalEP26   = Object.values(epMap).reduce((s, r) => s + (r.ep_aus26 || 0), 0);
  const totalEP27   = Object.values(epMap).reduce((s, r) => s + (r.ep_aus27 || 0), 0);

  // Header
  let html = `
    <div class="gp-summary">
      <div class="gp-sum-card">
        <div class="gp-sum-label">Gesamthaushalt 2026 (§1 ThürHhG)</div>
        <div class="gp-sum-value">${fmtEUR(totalGP26, 2)}</div>
      </div>
      <div class="gp-sum-card">
        <div class="gp-sum-label">Gesamthaushalt 2027 (§1 ThürHhG)</div>
        <div class="gp-sum-value">${fmtEUR(totalGP27, 2)}</div>
      </div>
      <div class="gp-sum-card gp-sum-parsed">
        <div class="gp-sum-label">Geparst 2026 (Summe EP-Dateien)</div>
        <div class="gp-sum-value">${fmtEUR(totalEP26, 2)}</div>
        <div class="gp-sum-abw">${fmtAbw(totalEP26, totalGP26)}</div>
      </div>
    </div>
    <table class="gp-table">
      <thead>
        <tr>
          <th>EP</th>
          <th>Bezeichnung</th>
          <th class="num gp-hide-mobile">Einnahmen<br>2026</th>
          <th class="num">Ausgaben 2026<br>(§1 Referenz)</th>
          <th class="num gp-hide-mobile">Ausgaben 2026<br>(geparst)</th>
          <th class="num">Abw.%</th>
          <th class="num gp-hide-mobile">Ausgaben 2027<br>(Referenz)</th>
          <th class="num gp-hide-mobile">Personal<br>2026</th>
        </tr>
      </thead>
      <tbody>
  `;

  for (const row of gpRows) {
    const ep   = row.einzelplan;
    const ep2  = epMap[ep] || {};
    const gp26 = row.ausgaben_2026 || 0;
    const ep26 = ep2.ep_aus26 || 0;
    const abw  = gp26 > 0 ? (ep26 - gp26) / gp26 * 100 : null;
    const abwClass = abw === null ? "" : Math.abs(abw) <= 5 ? "abw-ok" : "abw-warn";
    const abwStr   = abw === null
      ? "<span class='gp-na'>–</span>"
      : `<span class="${abwClass}">${abw >= 0 ? "+" : ""}${abw.toFixed(1)} %</span>`;
    const nStr = ep2.n_stellen ? `(${ep2.n_stellen} Titel)` : "";

    html += `
      <tr>
        <td><span class="ep-badge">EP ${ep}</span></td>
        <td>${row.bezeichnung || "–"}</td>
        <td class="num gp-hide-mobile">${fmtEURFull(row.einnahmen_2026)}</td>
        <td class="num ref-val">${fmtEURFull(gp26)}</td>
        <td class="num gp-hide-mobile">${ep26 > 0 ? fmtEURFull(ep26) : "<span class='gp-na'>–</span>"} <span class="gp-n">${nStr}</span></td>
        <td class="num">${abwStr}</td>
        <td class="num gp-hide-mobile">${fmtEURFull(row.ausgaben_2027)}</td>
        <td class="num gp-hide-mobile">${fmtEURFull(row.personal_aus_2026)}</td>
      </tr>
    `;
  }

  html += `
      </tbody>
      <tfoot>
        <tr class="tfoot-row">
          <td colspan="2">Gesamt</td>
          <td class="num gp-hide-mobile">–</td>
          <td class="num ref-val">${fmtEURFull(totalGP26)}</td>
          <td class="num gp-hide-mobile">${fmtEURFull(totalEP26)}</td>
          <td class="num">${fmtAbw(totalEP26, totalGP26)}</td>
          <td class="num gp-hide-mobile ref-val">${fmtEURFull(totalGP27)}</td>
          <td class="num gp-hide-mobile">–</td>
        </tr>
      </tfoot>
    </table>
    <p class="gp-hinweis">
      Referenz: §1 Thüringer Haushaltsgesetz 2026/2027 (Teil I A Gesamtplan) ·
      Geparst: Summe aller Haushaltsstellen-Titel HGr. 4–9 je EP ·
      Abweichungen entstehen durch Einnahmetitel (HGr. 0–3) und Parser-Unschärfen.
    </p>
  `;

  container.innerHTML = html;
}

async function openSchuldenModal() {
  document.getElementById("schulden-modal").classList.remove("hidden");
  await renderVerschuldungDetails();
}

function fmtAbw(ist, soll) {
  if (!soll) return "–";
  const pct = (ist - soll) / soll * 100;
  const cls = Math.abs(pct) <= 5 ? "abw-ok" : "abw-warn";
  return `<span class="${cls}">${pct >= 0 ? "+" : ""}${pct.toFixed(1)} %</span>`;
}

// ── Haushalt-Explorer ─────────────────────────────────────────────────────────
async function runHaushaltExplorer(overrides = {}) {
  const ep     = overrides.ep     ?? document.getElementById("f-ep").value;
  const hgr    = overrides.hgr    ?? document.getElementById("f-hgr").value;
  const jahr   = document.getElementById("f-jahr").value;
  const search = overrides.search ?? document.getElementById("f-search").value.trim();

  const cond = [];
  if (ep)     cond.push(`e.einzelplan = '${esc(ep)}'`);
  if (hgr)    cond.push(`e.hauptgruppe = '${esc(hgr)}'`);
  if (search) cond.push(`(LOWER(e.titel_name) LIKE LOWER('%${esc(search)}%') OR LOWER(e.kapitel_name) LIKE LOWER('%${esc(search)}%'))`);

  const where = cond.length ? "WHERE " + cond.join(" AND ") : "";
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
      { key: "ministerium",      label: "Ministerium" },
      { key: "kapitel",          label: "Kap." },
      { key: "titel",            label: "Titel" },
      { key: "titel_name",       label: "Bezeichnung" },
      { key: "hauptgruppe_name", label: "Art" },
      { key: "betrag",           label: "Betrag", num: true, format: fmtEURFull },
    ],
    sumCol: "betrag",
    emptyText: "Keine Treffer – Filter anpassen.",
  });
}

// ── Stellenplan-Explorer ──────────────────────────────────────────────────────
async function runStellenExplorer() {
  const ep        = (el("s-ep")?.value || "").trim();
  const typ       = (el("s-typ")?.value || "").trim();
  const besoldung = (el("s-besoldung")?.value || "").trim();
  const bez       = (el("s-bezeichnung")?.value || "").trim();

  const container = document.getElementById("stellen-result");
  container.innerHTML = `<p style="padding:1.2rem;color:var(--gray-400)">Lade …</p>`;

  const cond = [];
  if (ep)        cond.push(`s.einzelplan = '${esc(ep)}'`);
  if (typ)       cond.push(`s.typ = '${esc(typ)}'`);
  if (besoldung) cond.push(`LOWER(s.besoldung) LIKE LOWER('%${esc(besoldung)}%')`);
  if (bez)       cond.push(`LOWER(s.bezeichnung) LIKE LOWER('%${esc(bez)}%')`);

  const where = cond.length ? "WHERE " + cond.join(" AND ") : "";
  const sql = `
    SELECT s.einzelplan, s.kapitel, s.besoldung, s.laufbahn, s.bezeichnung, s.typ,
           s.stellen_2025, s.stellen_2026, s.stellen_2027,
           s.kw_stellen, s.kw_ab_jahr
    FROM haus.stellenplan s
    ${where}
    ORDER BY s.einzelplan, s.typ, s.besoldung DESC, s.stellen_2026 DESC
    LIMIT 400
  `;

  const rows = await query(sql);

  if (!rows.length) {
    const hinweis = ep
      ? `Keine Stellenplan-Einträge für EP ${ep} gefunden. Möglicherweise sind für diesen Einzelplan keine detaillierten Stellenplandaten verfügbar.`
      : "Keine Treffer – Filter anpassen.";
    container.innerHTML = `<p style="padding:1.2rem;color:var(--gray-400)">${hinweis}</p>`;
    return;
  }

  // Summen berechnen
  const sum25 = rows.reduce((s, r) => s + (r.stellen_2025 || 0), 0);
  const sum26 = rows.reduce((s, r) => s + (r.stellen_2026 || 0), 0);
  const sum27 = rows.reduce((s, r) => s + (r.stellen_2027 || 0), 0);

  const laufbahnLabel = { hD: "Höherer Dienst", gD: "Gehobener Dienst", mD: "Mittlerer Dienst" };

  const tbody = rows.map(r => {
    const kw = r.kw_stellen ? `<span class="kw-badge" title="Künftig wegfallend ab ${r.kw_ab_jahr || '?'}">kw ${r.kw_stellen}</span>` : "";
    const epKurz = EP_KURZ[r.einzelplan] || `EP${r.einzelplan}`;
    return `<tr>
      <td><span class="ep-badge" style="font-size:.72rem" title="EP ${r.einzelplan}">${epKurz}</span></td>
      <td><span class="bes-badge ${r.typ === 'Beamter' ? 'badge-beamter' : 'badge-tarif'}">${r.besoldung || "–"}</span></td>
      <td class="small-text">${r.laufbahn || r.typ || ""}</td>
      <td>${r.bezeichnung || "–"}${kw}</td>
      <td class="num">${fmtN(r.stellen_2025)}</td>
      <td class="num stellen-2026">${fmtN(r.stellen_2026)}</td>
      <td class="num">${fmtN(r.stellen_2027)}</td>
    </tr>`;
  }).join("");

  container.innerHTML = `
    <table class="stellen-table">
      <thead><tr>
        <th>Ministerium</th><th>Gruppe</th><th>Laufbahn/Art</th>
        <th>Bezeichnung</th>
        <th class="num">2025</th><th class="num">2026</th><th class="num">2027</th>
      </tr></thead>
      <tbody>${tbody}</tbody>
      <tfoot><tr class="tfoot-row">
        <td colspan="4">Summe (${rows.length} Positionen)</td>
        <td class="num">${fmtN(sum25)}</td>
        <td class="num stellen-2026">${fmtN(sum26)}</td>
        <td class="num">${fmtN(sum27)}</td>
      </tr></tfoot>
    </table>
  `;
}

// ── Tabelle rendern (generisch) ───────────────────────────────────────────────
function renderTable(rows, container, { columns, sumCol, emptyText = "Keine Daten." }) {
  if (!rows.length) {
    container.innerHTML = `<p style="padding:1.2rem;color:var(--gray-400)">${emptyText}</p>`;
    return;
  }
  const sum   = sumCol ? rows.reduce((s, r) => s + (r[sumCol] ?? 0), 0) : null;
  const thead = columns.map(c => `<th>${c.label}</th>`).join("");
  const tbody = rows.map(r =>
    `<tr>${columns.map(c => {
      const v   = r[c.key];
      const fmt = c.format ? c.format(v) : (v ?? "–");
      return `<td class="${c.num ? 'num' : ''}">${fmt}</td>`;
    }).join("")}</tr>`
  ).join("");
  const tfoot = sumCol ? `
    <tfoot><tr class="tfoot-row">
      ${columns.map((c, i) => i === 0
        ? `<td>Summe (${rows.length} Stellen)</td>`
        : c.key === sumCol ? `<td class="num">${fmtEURFull(sum)}</td>` : `<td></td>`
      ).join("")}
    </tr></tfoot>` : "";
  container.innerHTML = `
    <table><thead><tr>${thead}</tr></thead>
    <tbody>${tbody}</tbody>${tfoot}</table>
  `;
}

// ── Balkendiagramm (NL-Query Ergebnisse) ─────────────────────────────────────
function renderBarChart(rows, numKey) {
  const container = document.getElementById("chart-container");
  if (!numKey || rows.length < 2 || rows.length > 40) {
    container.innerHTML = "";
    return;
  }
  const labelKey = Object.keys(rows[0]).find(k => typeof rows[0][k] === "string");
  if (!labelKey) { container.innerHTML = ""; return; }

  const isEUR   = numKey.includes("ansatz") || numKey.includes("ist") || numKey.includes("personal") || numKey.includes("gesamt");
  const fmtVal  = isEUR ? fmtEURFull : fmtN;
  const maxVal  = Math.max(...rows.map(r => r[numKey] ?? 0));

  const bars = rows.slice(0, 20).map(r => {
    const pct   = maxVal > 0 ? ((r[numKey] ?? 0) / maxVal * 100).toFixed(1) : 0;
    const label = String(r[labelKey] ?? "").substring(0, 48);
    return `
      <div style="display:flex;align-items:center;gap:.6rem;margin:.25rem 0;">
        <div style="width:200px;font-size:.75rem;color:var(--gray-600);text-align:right;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${label}">${label}</div>
        <div style="flex:1;background:var(--gray-100);border-radius:3px;height:22px;position:relative;overflow:hidden;">
          <div style="width:${pct}%;background:var(--green);height:100%;border-radius:3px;transition:width .4s;"></div>
        </div>
        <div style="width:130px;font-size:.75rem;color:var(--gray-800);text-align:right;font-variant-numeric:tabular-nums;">${fmtVal(r[numKey])}</div>
      </div>
    `;
  }).join("");
  container.innerHTML = `<div style="padding:.5rem 0;">${bars}</div>`;
}

// ── Meta-Badge ────────────────────────────────────────────────────────────────
async function fillMeta() {
  try {
    const meta = await (await fetch("./data/meta.json")).json();
    document.getElementById("meta-badge").innerHTML =
      `Stand: ${meta.stand} · ${(meta.summe_2026_eur / 1e9).toFixed(1)} Mrd. € (2026)`;
    document.getElementById("footer-stand").textContent =
      `Datenstand: ${meta.stand} · Quelle: ${meta.quelle}`;
  } catch (_) {}
}

// ── SQL-Injection minimal absichern ───────────────────────────────────────────
function esc(s) {
  return String(s).replace(/'/g, "''");
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

    const sql  = data.sql;
    const rows = await query(sql);

    clearStatus();
    const section = document.getElementById("answer-section");
    section.classList.remove("hidden");
    document.getElementById("answer-title").textContent = `💬 ${question}`;
    document.getElementById("answer-sql").textContent   = sql;

    const cols    = rows.length ? Object.keys(rows[0]).map(k => ({
      key: k, label: k.replace(/_/g, " "),
      num: typeof rows[0][k] === "number",
      format: typeof rows[0][k] === "number"
        ? (k.includes("stellen") || k.includes("total") || k.includes("count") ? fmtN : fmtEURFull)
        : undefined,
    })) : [];
    const numCols = cols.filter(c => c.num);

    renderTable(rows, document.getElementById("table-container"), {
      columns: cols,
      sumCol: numCols.length === 1 ? numCols[0].key : null,
    });
    renderBarChart(rows, numCols[0]?.key);
    section.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    setStatus(`✗ ${e.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Fragen →";
  }
}

// ── Hilfsfunktion ─────────────────────────────────────────────────────────────
function el(id) { return document.getElementById(id); }

// ── Event-Listener – werden sofort beim Laden gesetzt ─────────────────────────
// WICHTIG: initUI() läuft synchron beim Seitenstart, BEVOR Daten geladen werden.
// So sind alle Listener garantiert aktiv, unabhängig davon ob später eine
// async-Funktion (renderTreemap etc.) einen Fehler wirft.
function initUI() {
  // NL-Suche
  el("nl-btn")?.addEventListener("click", handleNLQuery);
  el("nl-input")?.addEventListener("keydown", e => { if (e.key === "Enter") handleNLQuery(); });
  document.querySelectorAll(".quick-btn").forEach(btn =>
    btn.addEventListener("click", () => { el("nl-input").value = btn.dataset.q; handleNLQuery(); })
  );

  // Tabs
  document.querySelectorAll(".tab").forEach(btn =>
    btn.addEventListener("click", () => switchTab(btn.dataset.tab))
  );

  // Kacheln Jahr-Toggle (2026 / 2027)
  document.querySelectorAll("#kacheln-year-toggle .year-btn").forEach(btn =>
    btn.addEventListener("click", () => fillKacheln(btn.dataset.year))
  );

  // Treemap Jahr-Toggle (Struktur-Tab)
  document.querySelectorAll("#struktur-year-toggle .year-btn").forEach(btn =>
    btn.addEventListener("click", () => renderTreemap(btn.dataset.year))
  );

  // EP-Detail unter Treemap schließen
  el("ep-detail-close")?.addEventListener("click", () =>
    el("treemap-ep-detail")?.classList.add("hidden")
  );

  // Schulden-Modal schließen (× und Backdrop)
  el("schulden-close")?.addEventListener("click", () =>
    el("schulden-modal")?.classList.add("hidden")
  );
  el("schulden-modal")?.addEventListener("click", function(e) {
    if (e.target === this) this.classList.add("hidden");
  });

  // Haushalt-Explorer Filter
  el("filter-btn")?.addEventListener("click", () => runHaushaltExplorer());
  el("f-ep")?.addEventListener("change",   () => runHaushaltExplorer());
  el("f-hgr")?.addEventListener("change",  () => runHaushaltExplorer());
  el("f-jahr")?.addEventListener("change", () => runHaushaltExplorer());
  el("f-search")?.addEventListener("keydown", e => { if (e.key === "Enter") runHaushaltExplorer(); });

  // Stellen-Explorer Filter
  el("stellen-btn")?.addEventListener("click", async () => {
    await runStellenExplorer().catch(() => {});
    el("stellen-result")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  el("s-ep")?.addEventListener("change",         () => runStellenExplorer());
  el("s-typ")?.addEventListener("change",        () => runStellenExplorer());
  el("s-besoldung")?.addEventListener("keydown",   e => { if (e.key === "Enter") runStellenExplorer(); });
  el("s-bezeichnung")?.addEventListener("keydown", e => { if (e.key === "Enter") runStellenExplorer(); });

  // NL-Antwort schließen
  el("answer-close")?.addEventListener("click", () => el("answer-section")?.classList.add("hidden"));

  // API-Key Modal
  el("key-save")?.addEventListener("click", () => {
    const url = el("worker-url-input")?.value.trim();
    if (url) localStorage.setItem("worker_url", url);
    el("key-modal")?.classList.add("hidden");
    handleNLQuery();
  });
  el("key-cancel")?.addEventListener("click", () => el("key-modal")?.classList.add("hidden"));
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────
async function boot() {
  // 1. Event-Listener sofort setzen — unabhängig vom Datenladestand
  initUI();

  // 2. Datenbank laden
  setStatus("⏳ Lade Datenbank …", "info");
  try {
    await initDuckDB();
    clearStatus();
  } catch (e) {
    setStatus("⚠️ Daten nicht gefunden – bitte Pipeline ausführen.", "error");
    el("kacheln-grid").innerHTML =
      `<div class="kachel"><div class="k-label">⚠️ Hinweis</div>
       <div class="k-sub">Datenbank noch nicht generiert.</div></div>`;
    fillMeta().catch(() => {});
    return;
  }

  // 3. Daten laden – jeder Schritt mit eigenem Fehler-Handling
  await Promise.all([
    fillMeta().catch(e         => console.warn("fillMeta:", e)),
    fillKacheln().catch(e      => console.warn("fillKacheln:", e)),
    fillDropdowns().catch(e    => console.warn("fillDropdowns:", e)),
  ]);
  // 4. Initiales Rendering: Struktur (Standard-Tab) + Haushalt im Hintergrund
  renderTreemap().catch(e => console.warn("Treemap:", e));
  runHaushaltExplorer().catch(e => console.warn("Explorer:", e));
}

boot();
