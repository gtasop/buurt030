# Buurt030

Explore, compare and find Utrecht's 111 neighbourhoods (buurten), in Dutch and English.
Built entirely on free open data. No API keys anywhere.

- **Map**: colour by ~50 indicators, now or as change since 2019. Zoom, search by name or postcode.
- **Profile**: what stands out, buurten with the most similar profile, how it changed
  2019–2025 against the city, and every indicator with a city-wide strip plot.
- **Compare** two buurten side by side.
- **Find your buurt**: quick picks (family, car-free, lively, …), weighted criteria and a
  WOZ budget. Every buurt gets a 0–100 match score from its percentile rank on your criteria.
- **Use it from your own AI**: the Buurt030 MCP connector (`/mcp`) lets Claude, ChatGPT or
  any MCP client search, profile, compare and match buurten. The visitor's own AI account
  does the reasoning, so the site needs no AI keys and pays nothing for AI.

## Data sources

| Source | What | API |
|---|---|---|
| CBS *Kerncijfers wijken en buurten* 2019–2025 (84583NED … 86165NED), CC BY 4.0 | Population, households, housing, WOZ, income, work, care, energy, cars, distances to services | `opendata.cbs.nl/ODataApi` |
| CBS wijk- en buurtkaart 2025 via PDOK | Buurt and wijk boundaries | `api.pdok.nl/cbs/wijken-en-buurten-2025/ogc/v1` |
| OpenStreetMap via Overpass, © OpenStreetMap contributors (ODbL) | Playgrounds, parks, cafés/restaurants, supermarkets, stops, schools, GPs/pharmacies, libraries, sports | `overpass-api.de` (+ mirrors) |

Each indicator uses the newest CBS edition that has a value (CBS publishes income, labour,
care and energy about a year later), and the app marks older figures. CBS leaves out
figures for small populations. Utrecht's buurt boundaries did not change 2019–2025, so
trends compare like with like.

## Project layout

```
scripts/build_data.py   download + join + compute indicators -> web/data/buurten.json
web/index.html          the app (single page, d3 from cdnjs)
web/data/buurten.json   generated data (~415 KB, committed so deploys need no build)
functions/              MCP connector (Firebase Cloud Function "mcp", Node 22)
  index.js              MCP server: 6 read-only tools
  lib.js                search, profile, compare, rank, match (same logic as the site)
firebase.json           Hosting (web/) + /mcp rewrite to the function
```

## Run locally

```bash
python scripts/build_data.py            # refresh data (cached in cache/; --refresh to re-download)
cd web && python -m http.server 8000    # open http://localhost:8000
```

Python 3.10+ standard library only.

## Deploy (Firebase)

```bash
npx firebase-tools login
npx firebase-tools deploy --only hosting      # the website (free Spark plan is enough)
npx firebase-tools deploy --only functions    # the MCP connector (needs the Blaze plan)
```

Cloud Functions require the pay-as-you-go **Blaze** plan. The connector is small
(read-only, max 3 instances, 256 MiB) and normally stays within the free monthly quota.
Set a budget alert in Google Cloud to be safe.

## Use the connector

URL: `https://<your-site>.web.app/mcp` (Streamable HTTP, no authentication)

- **Claude**: Settings → Connectors → Add custom connector → paste the URL.
- **ChatGPT**: Settings → Apps & Connectors → Advanced → Developer mode → create a connector
  with the URL. Availability depends on your plan.

Tools: `list_indicators`, `find_buurt`, `get_buurt`, `compare_buurten`, `rank_buurten`,
`match_buurten`. Try: *"Which Utrecht buurten suit a family with a toddler, no car and a
€450k budget? Explain the trade-offs."*

## Ideas

- More layers: RIVM/Luchtmeetnet air quality, Klimaateffectatlas heat stress, trees, BAG building age
- 100 m grid and 6-digit postcode statistics (CBS) for finer detail
