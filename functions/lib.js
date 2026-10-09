// Data access and scoring for the Buurt030 connector. Mirrors the logic in
// web/index.html (percentile ranks, match scores, similarity) so the AI gets
// the same answers a visitor sees on the site.

const path = require("path");

const D = require(path.join(__dirname, "data", "buurten.json"));
const IND = Object.fromEntries(D.indicators.map((i) => [i.key, i]));
const byCode = Object.fromEntries(D.buurten.map((b) => [b.code, b]));
const YEARS = D.meta.series_years;

// Counts and sizes describe how big a buurt is, not what it is like.
const NOT_DISTINCTIVE = new Set(["pop", "homes", "area", "o_food", "o_super", "o_play", "o_park", "o_sport", "o_health", "o_school", "o_library"]);
const NOT_FOR_SIMILARITY = new Set([...NOT_DISTINCTIVE, "births", "schools3"]);

const value = (b, k) => (b && b.v[k] ? b.v[k][0] : null);
const year = (b, k) => (b && b.v[k] ? b.v[k][1] : null);
const cityVal = (k) => (D.city[k] ? D.city[k][0] : null);
const wijkShort = (n) => n.replace(/^Wijk \d+\s*/, "");

const sortedCache = {};
function levelVals(k) {
  if (!sortedCache[k]) sortedCache[k] = D.buurten.map((b) => value(b, k)).filter((v) => v != null).sort((a, b) => a - b);
  return sortedCache[k];
}
function bisect(arr, v, right) {
  let lo = 0, hi = arr.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (right ? arr[mid] <= v : arr[mid] < v) lo = mid + 1; else hi = mid; }
  return lo;
}
function pctRank(k, v) {
  const vals = levelVals(k);
  const below = bisect(vals, v, false), upto = bisect(vals, v, true);
  return (below + (upto - below) / 2) / vals.length;
}

function fmt(ind, v) {
  if (v == null) return null;
  const n = (d) => v.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
  switch (ind.fmt) {
    case "pct": return (v < 10 ? n(1) : n(0)) + "%";
    case "eur": return "€" + (Math.round(v / 100) * 100).toLocaleString("en-GB");
    case "dec1": return n(1);
    case "dec2": return n(2);
    default: return n(0);
  }
}
const unitOf = (ind) => (ind.unit && ind.fmt !== "pct" && ind.fmt !== "eur" ? ind.unit : "");

function changeOf(arr, ind) {
  if (!arr) return null;
  const i0 = arr.findIndex((v) => v != null);
  let i1 = arr.length - 1; while (i1 >= 0 && arr[i1] == null) i1--;
  if (i0 < 0 || i1 <= i0) return null;
  if (ind.trend === "rel") return arr[i0] ? (arr[i1] / arr[i0] - 1) * 100 : null;
  return arr[i1] - arr[i0];
}
function fmtChange(ind, d) {
  if (d == null) return null;
  const s = d > 0 ? "+" : d < 0 ? "-" : "±";
  if (ind.trend === "rel") return `${s}${Math.abs(d).toFixed(Math.abs(d) < 10 ? 1 : 0)}%`;
  if (ind.trend === "pp") return `${s}${Math.abs(d).toFixed(1)} percentage points`;
  return `${s}${Math.abs(d).toFixed(1)}`;
}

const norm = (s) => String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").trim();

function findBuurten(query, limit = 10) {
  const q = norm(query);
  if (!q) return [];
  const pc = q.replace(/\s/g, "").slice(0, 4);
  const hits = D.buurten.filter((b) =>
    b.code.toLowerCase() === q || norm(b.name).includes(q) || norm(wijkShort(b.wijk)).includes(q) || (/^\d{4}$/.test(pc) && b.postcode === pc));
  hits.sort((a, b) => (norm(a.name) === q ? -1 : 0) - (norm(b.name) === q ? -1 : 0));
  return hits.slice(0, limit).map(brief);
}

function resolve(ref) {
  if (!ref) return null;
  if (byCode[ref]) return byCode[ref];
  const q = norm(ref);
  return D.buurten.find((b) => norm(b.name) === q) || D.buurten.find((b) => norm(b.name).includes(q)) || null;
}

function brief(b) {
  return { code: b.code, name: b.name, wijk: wijkShort(b.wijk), postcode_area: b.postcode || null, residents: value(b, "pop") };
}

function indicatorRow(b, ind) {
  const v = value(b, ind.key);
  return {
    key: ind.key,
    indicator: ind.label,
    value: v,
    display: v == null ? "not published" : `${fmt(ind, v)}${unitOf(ind) ? " " + unitOf(ind) : ""}`,
    year: year(b, ind.key),
    utrecht: fmt(ind, cityVal(ind.key)),
    percentile_among_buurten: v == null ? null : Math.round(pctRank(ind.key, v) * 100),
  };
}

function listIndicators() {
  return D.indicators.map((i) => ({
    key: i.key, group: i.group, label: i.label, label_nl: i.label_nl, unit: unitOf(i) || (i.fmt === "pct" ? "%" : i.fmt === "eur" ? "€" : ""),
    utrecht_value: fmt(i, cityVal(i.key)), source: i.source, has_trend_2019_2025: Boolean(i.trend), description: i.desc || undefined,
  }));
}

function profile(b) {
  const groups = {};
  for (const ind of D.indicators) (groups[ind.group] ||= []).push(indicatorRow(b, ind));
  const standouts = D.indicators
    .filter((i) => !NOT_DISTINCTIVE.has(i.key) && value(b, i.key) != null && levelVals(i.key).length >= 40)
    .map((i) => ({ i, p: pctRank(i.key, value(b, i.key)) }))
    .sort((x, y) => Math.abs(y.p - 0.5) - Math.abs(x.p - 0.5))
    .slice(0, 5)
    .map(({ i, p }) => `${i.label}: ${fmt(i, value(b, i.key))}${unitOf(i) ? " " + unitOf(i) : ""} (${p >= 0.5 ? `higher than ${Math.min(99, Math.round(p * 100))}%` : `lower than ${Math.min(99, Math.round((1 - p) * 100))}%`} of Utrecht's buurten; city ${fmt(i, cityVal(i.key))})`);
  const trends = D.indicators.filter((i) => i.trend && b.s[i.key]).map((i) => ({
    indicator: i.label,
    [`change_${YEARS[0]}_${YEARS[YEARS.length - 1]}`]: fmtChange(i, changeOf(b.s[i.key], i)),
    utrecht_change: fmtChange(i, changeOf(D.city_series[i.key], i)),
    series: Object.fromEntries(YEARS.map((y, n) => [y, b.s[i.key][n]])),
  }));
  return { ...brief(b), what_stands_out: standouts, indicators: groups, trends, similar_buurten: similar(b, 4) };
}

function similar(b, n = 4) {
  const keys = D.indicators.filter((i) => !NOT_FOR_SIMILARITY.has(i.key)).map((i) => i.key);
  const out = [];
  for (const o of D.buurten) {
    if (o === b) continue;
    const diffs = [];
    for (const k of keys) {
      const a = value(b, k), c = value(o, k);
      if (a == null || c == null) continue;
      diffs.push(pctRank(k, c) - pctRank(k, a));
    }
    if (diffs.length < 15) continue;
    const rms = Math.sqrt(diffs.reduce((s, d) => s + d * d, 0) / diffs.length);
    out.push({ ...brief(o), similarity_pct: Math.max(0, Math.round(100 * (1 - rms * 2))) });
  }
  return out.sort((x, y) => y.similarity_pct - x.similarity_pct).slice(0, n);
}

function compare(list) {
  return {
    buurten: list.map(brief),
    indicators: D.indicators.map((i) => ({
      key: i.key, indicator: i.label, unit: unitOf(i) || undefined, utrecht: fmt(i, cityVal(i.key)),
      values: Object.fromEntries(list.map((b) => [b.name, value(b, i.key) == null ? "not published" : fmt(i, value(b, i.key))])),
    })),
  };
}

function rank(key, order, limit) {
  const ind = IND[key];
  const rows = D.buurten.filter((b) => value(b, key) != null && (value(b, "pop") || 0) >= 200)
    .sort((a, b) => (order === "lowest" ? value(a, key) - value(b, key) : value(b, key) - value(a, key)))
    .slice(0, limit)
    .map((b, n) => ({ rank: n + 1, ...brief(b), value: fmt(ind, value(b, key)) }));
  return { indicator: ind.label, unit: unitOf(ind) || undefined, utrecht: fmt(ind, cityVal(key)), order, results: rows };
}

function match(criteria, maxWoz, limit) {
  const crit = criteria.filter((c) => IND[c.key]).map((c) => ({ key: c.key, dir: c.direction === "low" ? "low" : "high", w: Math.min(3, Math.max(1, Math.round(c.weight || 1))) }));
  const total = crit.reduce((s, c) => s + c.w, 0) || 1;
  const rows = [];
  let excluded = 0;
  for (const b of D.buurten) {
    const woz = value(b, "woz"), pop = value(b, "pop");
    if ((maxWoz != null && (woz == null || woz > maxWoz)) || pop == null || pop < 200) { excluded++; continue; }
    let have = 0, acc = 0;
    const parts = [];
    for (const c of crit) {
      const v = value(b, c.key);
      if (v == null) continue;
      let p = pctRank(c.key, v);
      if (c.dir === "low") p = 1 - p;
      acc += p * c.w; have += c.w;
      parts.push({ indicator: IND[c.key].label, value: fmt(IND[c.key], v), fit_pct: Math.round(p * 100) });
    }
    if (crit.length && have / total < 0.5) { excluded++; continue; }
    const score = Math.round((crit.length ? acc / have : 0.5) * (crit.length ? Math.sqrt(have / total) : 1) * 100);
    rows.push({ ...brief(b), average_home_value_woz: fmt(IND.woz, woz), match_score: score, criteria: parts });
  }
  rows.sort((a, b) => b.match_score - a.match_score);
  return {
    how_scored: "Each criterion: the buurt's percentile rank among Utrecht's buurten (flipped for 'low'), weighted 1-3 and averaged. 100 = best on every criterion. Buurten with fewer than 200 residents, or over the WOZ budget, are left out.",
    criteria_used: crit.map((c) => ({ key: c.key, indicator: IND[c.key].label, direction: c.dir, weight: c.w })),
    unknown_keys: criteria.filter((c) => !IND[c.key]).map((c) => c.key),
    excluded_buurten: excluded,
    results: rows.slice(0, limit),
  };
}

module.exports = { D, IND, YEARS, findBuurten, resolve, listIndicators, profile, compare, rank, match, brief };
