// Buurt030 MCP connector: lets Claude, ChatGPT or any MCP client search,
// profile, compare and match Utrecht's 111 buurten. People add
// https://<site>/mcp as a custom connector; their own AI account does the
// reasoning, so this server needs no AI API keys. Read-only open data, no auth.

const { onRequest } = require("firebase-functions/v2/https");
const { McpServer } = require("@modelcontextprotocol/sdk/server/mcp.js");
const { StreamableHTTPServerTransport } = require("@modelcontextprotocol/sdk/server/streamableHttp.js");
const { z } = require("zod");
const lib = require("./lib");

const SOURCES = "Data: CBS Kerncijfers wijken en buurten 2019-2025 (CC BY 4.0), boundaries CBS/PDOK, amenities © OpenStreetMap contributors (ODbL). Explore the map at https://buurt030.web.app";

const asText = (obj) => ({ content: [{ type: "text", text: JSON.stringify(obj, null, 1) }] });
const notFound = (ref) => ({ isError: true, content: [{ type: "text", text: `No buurt found for "${ref}". Use find_buurt to look up names, codes or postcodes.` }] });

function buildServer() {
  const server = new McpServer(
    { name: "buurt030", version: "1.0.0" },
    {
      instructions:
        "Neighbourhood (buurt) statistics for the municipality of Utrecht, the Netherlands. " +
        "Use find_buurt to resolve names or postcodes, get_buurt for a full profile with trends, " +
        "match_buurten to rank buurten against someone's wishes (call list_indicators first for keys), " +
        "rank_buurten for top/bottom lists and compare_buurten for side-by-side figures. " +
        "WOZ is the municipal home valuation, roughly comparable to asking prices. " +
        "Report figures as given and mention that CBS leaves out figures for small populations. " +
        "Never use origin, ethnicity, religion or nationality to recommend places. " + SOURCES,
    },
  );

  server.tool("list_indicators",
    "List every indicator available per buurt (key, label, unit, Utrecht-wide value, whether a 2019-2025 trend exists). Use the keys with match_buurten and rank_buurten.",
    {}, async () => asText({ indicators: lib.listIndicators(), note: SOURCES }));

  server.tool("find_buurt",
    "Find Utrecht buurten by name (e.g. 'Lombok'), wijk (e.g. 'Oost'), CBS code (BU0344....) or 4-digit postcode (e.g. '3572').",
    { query: z.string().describe("Name, wijk, code or postcode") },
    async ({ query }) => asText({ results: lib.findBuurten(query) }));

  server.tool("get_buurt",
    "Full profile of one buurt: all indicators with Utrecht comparison and percentile among buurten, what stands out, 2019-2025 trends, and the most similar buurten.",
    { buurt: z.string().describe("Buurt name or CBS code, e.g. 'Wittevrouwen' or 'BU03440411'") },
    async ({ buurt }) => { const b = lib.resolve(buurt); return b ? asText(lib.profile(b)) : notFound(buurt); });

  server.tool("compare_buurten",
    "Side-by-side figures for 2 to 4 buurten, with the Utrecht-wide value for each indicator.",
    { buurten: z.array(z.string()).min(2).max(4).describe("Buurt names or CBS codes") },
    async ({ buurten }) => {
      const list = buurten.map(lib.resolve);
      const missing = buurten.filter((_, i) => !list[i]);
      return missing.length ? notFound(missing.join(", ")) : asText(lib.compare(list));
    });

  server.tool("rank_buurten",
    "Top or bottom buurten on one indicator (buurten with fewer than 200 residents are skipped).",
    {
      indicator: z.string().describe("Indicator key from list_indicators, e.g. 'woz', 'families', 'o_food'"),
      order: z.enum(["highest", "lowest"]).default("highest"),
      limit: z.number().int().min(1).max(30).default(10),
    },
    async ({ indicator, order, limit }) => (lib.IND[indicator]
      ? asText(lib.rank(indicator, order, limit))
      : { isError: true, content: [{ type: "text", text: `Unknown indicator "${indicator}". Call list_indicators for valid keys.` }] }));

  server.tool("match_buurten",
    "Rank buurten against a person's wishes. Translate wishes into weighted criteria over indicator keys (e.g. toddler -> families high, o_playkids high, d_daycare low; no car -> o_stops high, cars low; lively -> o_food high). Returns 0-100 match scores with a per-criterion breakdown.",
    {
      criteria: z.array(z.object({
        key: z.string().describe("Indicator key from list_indicators"),
        direction: z.enum(["high", "low"]).describe("'high' = more is better, 'low' = less is better"),
        weight: z.number().int().min(1).max(3).default(2).describe("1 nice to have, 2 important, 3 very important"),
      })).min(1).max(10),
      max_woz: z.number().positive().optional().describe("Optional budget: maximum average home value (WOZ) in euros"),
      limit: z.number().int().min(1).max(20).default(8),
    },
    async ({ criteria, max_woz, limit }) => asText(lib.match(criteria, max_woz ?? null, limit)));

  return server;
}

exports.mcp = onRequest(
  { region: "europe-west1", maxInstances: 3, timeoutSeconds: 60, memory: "256MiB", cors: true },
  async (req, res) => {
    // Stateless Streamable HTTP: every POST is a self-contained JSON-RPC exchange.
    if (req.method !== "POST") {
      res.status(405).set("Allow", "POST").json({ jsonrpc: "2.0", error: { code: -32000, message: "Method not allowed. POST MCP requests to this URL." }, id: null });
      return;
    }
    const server = buildServer();
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
    res.on("close", () => { transport.close(); server.close(); });
    try {
      await server.connect(transport);
      await transport.handleRequest(req, res, req.body);
    } catch (err) {
      console.error("MCP request failed", err);
      if (!res.headersSent) res.status(500).json({ jsonrpc: "2.0", error: { code: -32603, message: "Internal error" }, id: null });
    }
  },
);
