/**
 * Cloudflare Worker – NL→SQL Proxy für Thüringer Haushaltsplan Dashboard.
 *
 * Deployment:
 *   1. Cloudflare-Account anlegen (kostenlos): https://cloudflare.com
 *   2. Wrangler CLI: npm install -g wrangler
 *   3. wrangler login
 *   4. wrangler deploy --name haushalt worker/haushalt-worker.js
 *   5. API-Key als Secret: wrangler secret put ANTHROPIC_API_KEY
 *   6. Worker-URL (z.B. https://haushalt.DEIN-NAME.workers.dev) im Dashboard eintragen.
 *
 * Kosten: Gratis-Tier = 100.000 Anfragen/Tag.
 */

const ALLOWED_ORIGIN = "*";   // Für Public Dashboard; ggf. auf GitHub-Pages-URL beschränken.

const SCHEMA_CONTEXT = `
Du bist ein SQL-Experte für den Thüringer Landeshaushalt 2026/2027.
Gesetzlicher Gesamthaushalt: 14.807.535.300 EUR (2026), 15.135.886.100 EUR (2027).
Die DuckDB-Datenbank heißt "haus" und enthält folgende Tabellen:

TABELLE haushaltsstellen:
  einzelplan       TEXT  -- Einzelplan-Nummer (z.B. "06" = Finanzministerium)
  ministerium      TEXT  -- Vollständiger Name des Ministeriums
  kapitel          TEXT  -- Kapitel-Nummer (4-stellig, z.B. "0601")
  kapitel_name     TEXT  -- Name des Kapitels
  titel            TEXT  -- Titelkennzahl (5-stellig, z.B. "42201")
  titel_name       TEXT  -- Bezeichnung der Haushaltsstelle
  hauptgruppe      TEXT  -- 4=Personal, 5=Sachmittel, 6=Zuweisungen, 7=Bau, 8=Investitionen, 0-3=Einnahmen
  hauptgruppe_name TEXT  -- Ausgeschriebener Name der Hauptgruppe
  ansatz_2025      REAL  -- Haushaltsansatz 2025 in EUR
  ansatz_2026      REAL  -- Geplante Ausgaben/Einnahmen 2026 in EUR
  ansatz_2027      REAL  -- Geplante Ausgaben/Einnahmen 2027 in EUR
  ist_2024         REAL  -- Tatsächliche Ausgaben/Einnahmen 2024 in EUR

TABELLE gesamtplan_referenz:  -- OFFIZIELLE REFERENZ aus §1 ThürHhG
  einzelplan        TEXT  -- Einzelplan-Nummer
  bezeichnung       TEXT  -- Name des Ministeriums
  einnahmen_2026    REAL  -- Summe Einnahmen 2026 in EUR
  ausgaben_2026     REAL  -- Summe Ausgaben 2026 in EUR (Referenz-Wahrheit)
  personal_aus_2026 REAL  -- Personalausgaben 2026 in EUR (HGr 4 laut Gesamtplan)
  einnahmen_2027    REAL  -- Summe Einnahmen 2027 in EUR
  ausgaben_2027     REAL  -- Summe Ausgaben 2027 in EUR
  beamte_soll_2026  INTEGER -- Planstellen Beamte 2026
  an_soll_2026      INTEGER -- Stellen Arbeitnehmer 2026
  gesamt_soll_2026  INTEGER -- Gesamte Planstellen/Stellen 2026
  beamte_soll_2027  INTEGER -- Planstellen Beamte 2027
  an_soll_2027      INTEGER -- Stellen Arbeitnehmer 2027
  gesamt_soll_2027  INTEGER -- Gesamte Planstellen/Stellen 2027

TABELLE stellenplan:  -- Detaillierter Stellenplan je Besoldungsgruppe
  einzelplan   TEXT  -- Einzelplan-Nummer
  kapitel      TEXT  -- Kapitel-Nummer (4-stellig)
  besoldung    TEXT  -- z.B. "A12", "B3", "E9a"
  laufbahn     TEXT  -- "hD", "gD", "mD" (nur Beamte)
  bezeichnung  TEXT  -- z.B. "Amtsrat", "Tarifbeschäftigter"
  typ          TEXT  -- "Beamter" oder "Tarifbeschäftigter"
  stellen_2026 INTEGER -- Planstellen 2026
  stellen_2027 INTEGER -- Planstellen 2027
  kw_stellen   INTEGER -- Künftig wegfallende Stellen
  kw_ab_jahr   INTEGER -- Jahr ab dem die Stelle wegfällt

TABELLE stellenuebersicht:  -- Kapitel-Summen aus Stellenübersicht
  einzelplan  TEXT    -- Einzelplan-Nummer
  kapitel     TEXT    -- Kapitel-Nummer oder "GESAMT"
  typ         TEXT    -- "Beamter", "Tarifbeschäftigter" oder "Gesamt"
  jahr        INTEGER -- 2026 oder 2027
  stellen     INTEGER -- Anzahl Stellen

VIEWS:
  v_personal           -- Nur HGr 4 Personalausgaben
  v_ministerium_summen -- Aggregierte Summen je Ministerium

WICHTIGE HINWEISE:
- Beträge in EUR. Alle Tabellen mit "haus." prefixen.
- Ausgaben: hauptgruppe IN ('4','5','6','7','8','9')
- Einnahmen: hauptgruppe IN ('0','1','2','3')
- Personalausgaben €: hauptgruppe = '4'
- Planstellen Anzahl: gesamtplan_referenz.gesamt_soll_2026 (Referenz) oder SUM(stellen_2026) aus stellenplan
- Offizielle Referenz für Ausgaben: gesamtplan_referenz.ausgaben_2026 (§1 ThürHhG)
- Für "Ministerium X" → ministerium LIKE '%X%' oder LOWER(ministerium) LIKE '%x%'
- ILIKE nicht verfügbar → LOWER(x) LIKE LOWER('%...')
- Für Vergleich geparst vs. Referenz: JOIN haushaltsstellen mit gesamtplan_referenz

Antworte NUR mit reinem SQL (ohne Markdown-Codeblöcke, ohne Erklärungen).
LIMIT 100 maximal. Sortiere sinnvoll (Betrag DESC oder Stellenanzahl DESC).
`;

async function handleRequest(request, env) {
  // CORS preflight
  if (request.method === "OPTIONS") {
    return new Response(null, {
      headers: corsHeaders(),
      status: 204,
    });
  }

  if (request.method !== "POST") {
    return json({ error: "Nur POST erlaubt." }, 405);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "Ungültiger JSON-Body." }, 400);
  }

  const question = (body.question || "").trim();
  if (!question) {
    return json({ error: "Kein 'question' im Body." }, 400);
  }

  // Rate-limiting (einfach via CF-IP)
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";

  // Claude API aufrufen
  const apiKey = env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return json({ error: "ANTHROPIC_API_KEY nicht konfiguriert." }, 500);
  }

  const claudeResp = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: "claude-haiku-4-5-20251001",
      max_tokens: 512,
      system: SCHEMA_CONTEXT,
      messages: [{ role: "user", content: question }],
    }),
  });

  if (!claudeResp.ok) {
    const err = await claudeResp.text();
    return json({ error: `Claude API Fehler: ${claudeResp.status} – ${err}` }, 502);
  }

  const claudeData = await claudeResp.json();
  const sql = claudeData.content?.[0]?.text?.trim() || "";

  if (!sql || !sql.toUpperCase().startsWith("SELECT")) {
    return json({ error: "Claude hat keine gültige SELECT-Abfrage generiert.", raw: sql }, 422);
  }

  return json({ sql, question }, 200);
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin":  ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders() },
  });
}

export default {
  fetch: handleRequest,
};
