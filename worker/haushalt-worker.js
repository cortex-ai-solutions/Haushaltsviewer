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
Die SQLite-Datenbank heißt "haus" und enthält folgende Tabellen:

TABELLE haushaltsstellen:
  einzelplan       TEXT  -- Einzelplan-Nummer (z.B. "06" = Finanzministerium)
  ministerium      TEXT  -- Vollständiger Name des Ministeriums
  kapitel          TEXT  -- Kapitel-Nummer (4-stellig, z.B. "0601")
  kapitel_name     TEXT  -- Name des Kapitels
  titel            TEXT  -- Titelkennzahl (5-stellig, z.B. "42201")
  titel_name       TEXT  -- Bezeichnung der Haushaltsstelle
  hauptgruppe      TEXT  -- Erste Stelle des Titels: 4=Personal, 5=Sachmittel, 6=Zuweisungen, 7=Bau, 8=Investitionen
  hauptgruppe_name TEXT  -- Ausgeschriebener Name der Hauptgruppe
  ansatz_2026      REAL  -- Geplante Ausgaben 2026 in EUR
  ansatz_2027      REAL  -- Geplante Ausgaben 2027 in EUR
  ist_2024         REAL  -- Tatsächliche Ausgaben 2024 in EUR

TABELLE einzelplaene:
  nr      TEXT  -- Einzelplan-Nummer
  name    TEXT  -- Name des Ministeriums
  pdf_url TEXT  -- URL des Quell-PDFs

VIEWS:
  v_personal          -- Nur Personalausgaben (hauptgruppe = '4')
  v_ministerium_summen -- Summen je Ministerium (personal_2026, personal_2027, gesamt_2026, gesamt_2027)

WICHTIGE HINWEISE:
- Beträge sind in EUR (ganze Zahlen oder Dezimalzahlen)
- Alle Tabellen sind über "haus." zu prefixen (z.B. SELECT * FROM haus.haushaltsstellen)
- Personalausgaben: WHERE hauptgruppe = '4'
- Für "Personal" oder "Beschäftigte" → hauptgruppe = '4'
- Für "Investitionen" → hauptgruppe IN ('7','8')
- Für "laufende Ausgaben" → hauptgruppe IN ('4','5','6')
- ILIKE nicht verfügbar → stattdessen LOWER(x) LIKE LOWER('%...')

Antworte NUR mit reinem SQL (ohne Markdown-Codeblöcke, ohne Erklärungen).
Gib immer maximal 100 Zeilen zurück (LIMIT 100).
Sortiere nach Betrag absteigend wenn sinnvoll.
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
