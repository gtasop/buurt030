"""Build the data file for Buurt030, the Utrecht neighbourhood explorer.

Pulls neighbourhood statistics from CBS (2019-2025), boundaries from PDOK and
amenities from OpenStreetMap (Overpass), and writes one compact JSON file the
web app reads: web/data/buurten.json.

Standard library only. Run from anywhere:

    python scripts/build_data.py            # use cached downloads when present
    python scripts/build_data.py --refresh  # download everything again
"""

import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
OUT = ROOT / "web" / "data" / "buurten.json"

GEMEENTE = "GM0344"  # Utrecht
# CBS "Kerncijfers wijken en buurten", one table per edition year. Utrecht's
# 111 buurten kept the same codes and boundaries from 2019 to 2025, so the
# yearly figures compare like with like.
CBS_TABLES = {
    2019: "84583NED", 2020: "84799NED", 2021: "85039NED", 2022: "85318NED",
    2023: "85618NED", 2024: "85984NED", 2025: "86165NED",
}
# CBS fills a new edition in stages (income, energy and labour arrive a year
# or more later), so a buurt's current profile takes each indicator from the
# newest of these editions that has a value.
CURRENT_YEARS = [2025, 2024]
PDOK_YEAR = 2025
OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
USER_AGENT = "buurt030/0.2 (open data prototype)"

REFRESH = "--refresh" in sys.argv


# --------------------------------------------------------------------------
# Downloading


def fetch(url, data=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def cached_json(name, loader):
    CACHE.mkdir(exist_ok=True)
    path = CACHE / name
    if path.exists() and not REFRESH:
        return json.loads(path.read_text(encoding="utf-8"))
    print(f"  downloading {name} ...")
    data = loader()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def base_key(key):
    """CBS numbers every column (AantalInwoners_5); the number shifts between
    editions, the name does not."""
    return re.sub(r"_\d+$", "", key)


def load_cbs(table):
    def loader():
        q = urllib.parse.urlencode(
            {"$filter": f"substringof('{GEMEENTE[2:]}',WijkenEnBuurten)", "$format": "json"}
        )
        rows, url = [], f"https://opendata.cbs.nl/ODataApi/odata/{table}/TypedDataSet?{q}"
        while url:  # the OData API pages at 10,000 rows; follow odata.nextLink
            page = json.loads(fetch(url))
            rows += page["value"]
            url = page.get("odata.nextLink")
        return rows

    out = {}
    for r in cached_json(f"cbs_{table}.json", loader):
        code = r["WijkenEnBuurten"].strip()
        if code != GEMEENTE and not code.startswith(("WK" + GEMEENTE[2:], "BU" + GEMEENTE[2:])):
            continue  # the substring filter also matches other municipalities' codes
        norm = {}
        for k, v in r.items():
            norm.setdefault(base_key(k), v)  # a few names repeat under herkomst; we use none of them
        out[code] = norm
    return out


def load_pdok(collection):
    def loader():
        url = (
            f"https://api.pdok.nl/cbs/wijken-en-buurten-{PDOK_YEAR}/ogc/v1/collections/"
            f"{collection}/items?f=json&gemeentecode={GEMEENTE}&limit=1000"
        )
        return json.loads(fetch(url))

    return cached_json(f"pdok_{collection}_{PDOK_YEAR}.json", loader)["features"]


OSM_SELECTORS = [
    'nwr[leisure=playground]', 'nwr[leisure=park]',
    'nwr[amenity~"^(cafe|restaurant|bar|pub)$"]', 'nwr[shop=supermarket]',
    'node[public_transport=platform]', 'nwr[amenity=school]',
    'nwr[amenity~"^(doctors|pharmacy)$"]', 'nwr[amenity=library]',
    'nwr[leisure~"^(sports_centre|pitch)$"]',
]
OSM_CATEGORIES = ["playground", "park", "food", "supermarket", "stop", "school", "health", "library", "sport"]


def osm_category(tags):
    leisure, amenity = tags.get("leisure", ""), tags.get("amenity", "")
    if leisure == "playground":
        return "playground"
    if leisure == "park":
        return "park"
    if leisure.startswith("sports_centre") or leisure == "pitch":
        return "sport"
    if amenity in ("cafe", "restaurant", "bar", "pub"):
        return "food"
    if tags.get("shop") == "supermarket":
        return "supermarket"
    if tags.get("public_transport") == "platform":
        return "stop"
    if amenity == "school":
        return "school"
    if amenity in ("doctors", "pharmacy"):
        return "health"
    if amenity == "library":
        return "library"
    return None


def load_osm(bbox):
    def loader():
        s, w, n, e = bbox
        query = f"[out:json][timeout:150][bbox:{s},{w},{n},{e}];({';'.join(OSM_SELECTORS)};);out center tags;"
        body = urllib.parse.urlencode({"data": query}).encode()
        last = None
        for server in OVERPASS_SERVERS:  # public servers are often busy; try the next one
            try:
                return json.loads(fetch(server, data=body, timeout=200))
            except Exception as exc:  # noqa: BLE001 - any failure means "try another server"
                print(f"    {server} failed: {exc}")
                last = exc
                time.sleep(2)
        raise RuntimeError(f"All Overpass servers failed: {last}")

    return cached_json("osm_amenities.json", loader)["elements"]


# --------------------------------------------------------------------------
# Indicators
#
# `fn` computes a value from one CBS edition's row (column names without the
# _N suffix), so derived ratios never mix years. It returns None when CBS left
# a value out (it suppresses figures for small populations to protect privacy).
#
# `trend` marks indicators measured the same way in every edition 2019-2025;
# those get a yearly series. "pp" = compare in percentage points, "rel" = in %.


def val(r, key):
    v = r.get(key)
    return None if v is None else float(v)


def share(r, part, total, scale=100.0, min_total=50):
    p, t = val(r, part), val(r, total)
    if p is None or t is None or t < min_total:
        return None
    return p / t * scale


def per_1000(r, key, min_pop=200):
    return share(r, key, "AantalInwoners", 1000.0, min_pop)


def higher_edu(r):
    parts = [val(r, k) for k in ("BasisonderwijsVmboMbo1", "HavoVwoMbo24", "HboWo")]
    if any(p is None for p in parts) or sum(parts) < 50:
        return None
    return parts[2] / sum(parts) * 100


def thousand(key):
    return lambda r: None if val(r, key) is None else val(r, key) * 1000


CBS = "CBS Kerncijfers wijken en buurten"
OSM = "OpenStreetMap"

GROUP_NL = {
    "People": "Bewoners", "Housing": "Wonen", "Income & work": "Inkomen & werk", "Care": "Zorg",
    "Energy": "Energie", "Getting around": "Vervoer", "Nearby": "Nabijheid", "Area": "Gebied",
    "Amenities": "Voorzieningen",
}

# key, group, English label, Dutch label, unit (en, nl), fmt, fn, trend, descriptions (en, nl)
INDICATORS = [
    dict(key="pop", group="People", label="Residents", label_nl="Inwoners", fmt="int",
         fn=lambda r: val(r, "AantalInwoners"), trend="rel"),
    dict(key="density", group="People", label="Population density", label_nl="Bevolkingsdichtheid",
         unit="per km²", unit_nl="per km²", fmt="int", fn=lambda r: val(r, "Bevolkingsdichtheid")),
    dict(key="kids", group="People", label="Children (0–14)", label_nl="Kinderen (0–14)", fmt="pct",
         fn=lambda r: share(r, "k_0Tot15Jaar", "AantalInwoners"), trend="pp"),
    dict(key="young", group="People", label="Young adults (15–24)", label_nl="Jongvolwassenen (15–24)", fmt="pct",
         fn=lambda r: share(r, "k_15Tot25Jaar", "AantalInwoners"), trend="pp"),
    dict(key="adults", group="People", label="Ages 25–44", label_nl="25–44 jaar", fmt="pct",
         fn=lambda r: share(r, "k_25Tot45Jaar", "AantalInwoners"), trend="pp"),
    dict(key="seniors", group="People", label="Seniors (65+)", label_nl="65-plussers", fmt="pct",
         fn=lambda r: share(r, "k_65JaarOfOuder", "AantalInwoners"), trend="pp"),
    dict(key="single", group="People", label="One-person households", label_nl="Eenpersoonshuishoudens", fmt="pct",
         fn=lambda r: share(r, "Eenpersoonshuishoudens", "HuishoudensTotaal"), trend="pp"),
    dict(key="families", group="People", label="Households with children", label_nl="Huishoudens met kinderen",
         fmt="pct", fn=lambda r: share(r, "HuishoudensMetKinderen", "HuishoudensTotaal"), trend="pp"),
    dict(key="hhsize", group="People", label="Average household size", label_nl="Gemiddelde huishoudensgrootte",
         unit="people", unit_nl="personen", fmt="dec1", fn=lambda r: val(r, "GemiddeldeHuishoudensgrootte")),
    dict(key="births", group="People", label="Births", label_nl="Geboorten",
         unit="per 1,000 residents", unit_nl="per 1.000 inwoners", fmt="dec1",
         fn=lambda r: val(r, "GeboorteRelatief")),
    # Housing
    dict(key="homes", group="Housing", label="Homes", label_nl="Woningen", fmt="int",
         fn=lambda r: val(r, "Woningvoorraad"), trend="rel"),
    dict(key="woz", group="Housing", label="Average home value (WOZ)", label_nl="Gemiddelde WOZ-waarde", fmt="eur",
         fn=thousand("GemiddeldeWOZWaardeVanWoningen"), trend="rel",
         desc="Average WOZ value: the municipality's valuation of homes for property tax.",
         desc_nl="Gemiddelde WOZ-waarde: de waarde die de gemeente voor de belastingen vaststelt."),
    dict(key="owner", group="Housing", label="Owner-occupied homes", label_nl="Koopwoningen", fmt="pct",
         fn=lambda r: val(r, "Koopwoningen"), trend="pp"),
    dict(key="social", group="Housing", label="Housing association rentals", label_nl="Huur van woningcorporatie",
         fmt="pct", fn=lambda r: val(r, "InBezitWoningcorporatie"), trend="pp"),
    dict(key="privrent", group="Housing", label="Private rentals", label_nl="Particuliere huur", fmt="pct",
         fn=lambda r: val(r, "InBezitOverigeVerhuurders"), trend="pp"),
    dict(key="flats", group="Housing", label="Apartments", label_nl="Appartementen", fmt="pct",
         fn=lambda r: val(r, "PercentageMeergezinswoning")),
    dict(key="newbuild", group="Housing", label="Built in the last 10 years", label_nl="Gebouwd in de laatste 10 jaar",
         fmt="pct", fn=lambda r: val(r, "BouwjaarAfgelopenTienJaar")),
    # Income & work
    dict(key="income", group="Income & work", label="Average income per resident", label_nl="Gemiddeld inkomen per inwoner",
         fmt="eur", fn=thousand("GemiddeldInkomenPerInwoner")),
    dict(key="wealth", group="Income & work", label="Median household wealth", label_nl="Mediaan vermogen huishoudens",
         fmt="eur", fn=thousand("MediaanVermogenVanParticuliereHuish")),
    dict(key="poverty", group="Income & work", label="Residents in poverty", label_nl="Personen in armoede", fmt="pct",
         fn=lambda r: val(r, "PersonenInArmoede"),
         desc="Share of residents in a household with an income below the CBS poverty line.",
         desc_nl="Aandeel inwoners in een huishouden met een inkomen onder de armoedegrens van het CBS."),
    dict(key="highinc", group="Income & work", label="Households in top 20% income (NL)",
         label_nl="Huishoudens in hoogste 20% inkomen (NL)", fmt="pct",
         fn=lambda r: val(r, "k_20HuishoudensMetHoogsteInkomen")),
    dict(key="work", group="Income & work", label="Labour participation", label_nl="Nettoarbeidsparticipatie", fmt="pct",
         fn=lambda r: val(r, "Nettoarbeidsparticipatie"),
         desc="Share of 15–74 year olds in paid work.", desc_nl="Aandeel 15- tot 75-jarigen met betaald werk."),
    dict(key="selfemp", group="Income & work", label="Self-employed (of workers)", label_nl="Zelfstandigen (van werkenden)",
         fmt="pct", fn=lambda r: val(r, "PercentageZelfstandigen")),
    dict(key="edu", group="Income & work", label="Higher education (hbo/wo)", label_nl="Hoogopgeleid (hbo/wo)", fmt="pct",
         fn=higher_edu,
         desc="Share of 15–74 year olds whose highest completed education is hbo or university.",
         desc_nl="Aandeel 15- tot 75-jarigen met hbo of wo als hoogst behaalde opleiding."),
    dict(key="welfare", group="Income & work", label="On social assistance (bijstand)", label_nl="Bijstandsuitkering",
         unit="per 1,000 residents", unit_nl="per 1.000 inwoners", fmt="dec1",
         fn=lambda r: per_1000(r, "PersonenPerSoortUitkeringBijstand"), trend="pp1000"),
    # Care
    dict(key="youthcare", group="Care", label="Young people in youth care", label_nl="Jongeren met jeugdzorg", fmt="pct",
         fn=lambda r: val(r, "PercentageJongerenMetJeugdzorg")),
    dict(key="wmo", group="Care", label="Wmo support clients", label_nl="Wmo-cliënten",
         unit="per 1,000 residents", unit_nl="per 1.000 inwoners", fmt="int",
         fn=lambda r: val(r, "WmoClientenRelatief"),
         desc="Residents receiving municipal support under the Wmo (care and support at home).",
         desc_nl="Inwoners met een maatwerkvoorziening uit de Wmo (zorg en ondersteuning thuis)."),
    # Energy
    dict(key="gasfree", group="Energy", label="Gas-free homes", label_nl="Aardgasvrije woningen", fmt="pct",
         fn=lambda r: val(r, "AardgasvrijeWoningen")),
    dict(key="solar", group="Energy", label="Homes with solar panels", label_nl="Woningen met zonnepanelen", fmt="pct",
         fn=lambda r: val(r, "WoningenMetZonnestroom")),
    dict(key="district", group="Energy", label="Homes on district heating", label_nl="Woningen met stadsverwarming",
         fmt="pct", fn=lambda r: val(r, "PercentageWoningenMetStadsverwarming")),
    # Getting around
    dict(key="cars", group="Getting around", label="Cars per household", label_nl="Auto's per huishouden", fmt="dec2",
         fn=lambda r: val(r, "PersonenautoSPerHuishouden")),
    dict(key="carsdens", group="Getting around", label="Cars per km²", label_nl="Auto's per km²", fmt="int",
         fn=lambda r: val(r, "PersonenautoSNaarOppervlakte")),
    # Nearby
    dict(key="d_gp", group="Nearby", label="Distance to GP", label_nl="Afstand tot huisarts", unit="km", unit_nl="km",
         fmt="dec1", fn=lambda r: val(r, "AfstandTotHuisartsenpraktijk"),
         desc="Average road distance from homes to the nearest GP practice.",
         desc_nl="Gemiddelde afstand over de weg van woningen tot de dichtstbijzijnde huisartsenpraktijk."),
    dict(key="d_super", group="Nearby", label="Distance to large supermarket", label_nl="Afstand tot grote supermarkt",
         unit="km", unit_nl="km", fmt="dec1", fn=lambda r: val(r, "AfstandTotGroteSupermarkt")),
    dict(key="d_daycare", group="Nearby", label="Distance to daycare", label_nl="Afstand tot kinderdagverblijf",
         unit="km", unit_nl="km", fmt="dec1", fn=lambda r: val(r, "AfstandTotKinderdagverblijf")),
    dict(key="d_school", group="Nearby", label="Distance to primary school", label_nl="Afstand tot basisschool",
         unit="km", unit_nl="km", fmt="dec1", fn=lambda r: val(r, "AfstandTotSchool")),
    dict(key="schools3", group="Nearby", label="Primary schools within 3 km", label_nl="Basisscholen binnen 3 km",
         fmt="dec1", fn=lambda r: val(r, "ScholenBinnen3Km")),
    # Area
    dict(key="area", group="Area", label="Area", label_nl="Oppervlakte", unit="ha", unit_nl="ha", fmt="int",
         fn=lambda r: val(r, "OppervlakteTotaal")),
    dict(key="water", group="Area", label="Water", label_nl="Water", unit="% of area", unit_nl="% van oppervlakte",
         fmt="pct", fn=lambda r: share(r, "OppervlakteWater", "OppervlakteTotaal", min_total=1)),
]

# Amenity counts from OpenStreetMap, computed after point-in-polygon matching.
OSM_INDICATORS = [
    dict(key="o_food", group="Amenities", label="Cafés, bars & restaurants", label_nl="Cafés, bars & restaurants", fmt="int"),
    dict(key="o_super", group="Amenities", label="Supermarkets", label_nl="Supermarkten", fmt="int"),
    dict(key="o_play", group="Amenities", label="Playgrounds", label_nl="Speeltuinen", fmt="int"),
    dict(key="o_playkids", group="Amenities", label="Playgrounds per 1,000 children",
         label_nl="Speeltuinen per 1.000 kinderen", fmt="dec1"),
    dict(key="o_park", group="Amenities", label="Parks", label_nl="Parken", fmt="int"),
    dict(key="o_sport", group="Amenities", label="Sports fields & centres", label_nl="Sportvelden & -centra", fmt="int"),
    dict(key="o_health", group="Amenities", label="GPs & pharmacies", label_nl="Huisartsen & apotheken", fmt="int"),
    dict(key="o_school", group="Amenities", label="Schools", label_nl="Scholen", fmt="int"),
    dict(key="o_library", group="Amenities", label="Libraries", label_nl="Bibliotheken", fmt="int"),
    dict(key="o_stops", group="Getting around", label="Bus & tram stop platforms", label_nl="Bus- en tramperrons",
         unit="per km²", unit_nl="per km²", fmt="dec1",
         desc="OpenStreetMap stop platforms per km². A stop usually has one platform per direction.",
         desc_nl="Perrons van haltes per km² in OpenStreetMap. Een halte heeft meestal één perron per richting."),
]
OSM_COUNT_KEYS = {
    "food": "o_food", "supermarket": "o_super", "playground": "o_play", "park": "o_park",
    "sport": "o_sport", "health": "o_health", "school": "o_school", "library": "o_library",
}


# --------------------------------------------------------------------------
# Geometry helpers


LAT0, LON0 = 52.09, 5.1
KX = math.cos(math.radians(LAT0)) * 111_320  # metres per degree longitude
KY = 110_540  # metres per degree latitude


def project(lon, lat):
    """Local equirectangular projection to metres; plenty for one city."""
    return round((lon - LON0) * KX), round((lat - LAT0) * KY)


def simplify(points, tol):
    """Douglas-Peucker on a ring of (x, y) points in metres."""
    if len(points) < 5:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = points[a]
        bx, by = points[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1e-9
        best, idx = -1.0, None
        for i in range(a + 1, b):
            px, py = points[i]
            d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > best:
                best, idx = d, i
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    out = [p for p, k in zip(points, keep) if k]
    return out if len(out) >= 4 else points


def polygons_of(geom):
    return [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]


def project_geom(geom, tol):
    return [[simplify([project(*pt) for pt in ring], tol) for ring in poly] for poly in polygons_of(geom)]


def point_in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_polygons(x, y, polys):
    for poly in polys:
        if point_in_ring(x, y, poly[0]) and not any(point_in_ring(x, y, h) for h in poly[1:]):
            return True
    return False


# --------------------------------------------------------------------------


def main():
    print("Loading CBS tables")
    editions = {year: load_cbs(table) for year, table in CBS_TABLES.items()}
    years = sorted(editions)

    print("Loading PDOK boundaries")
    buurt_feats = load_pdok("buurten")
    wijk_feats = load_pdok("wijken")
    wijk_names = {f["properties"]["wijkcode"]: f["properties"]["wijknaam"] for f in wijk_feats}

    # Raw lon/lat polygons for point-in-polygon, bbox for the Overpass query.
    raw = {f["properties"]["buurtcode"]: polygons_of(f["geometry"]) for f in buurt_feats}
    lons = [c[0] for polys in raw.values() for p in polys for ring in p for c in ring]
    lats = [c[1] for polys in raw.values() for p in polys for ring in p for c in ring]
    bbox = (min(lats), min(lons), max(lats), max(lons))

    print("Loading OpenStreetMap amenities")
    osm_ok = True
    try:
        elements = load_osm(bbox)
    except RuntimeError as exc:
        print(f"  WARNING: {exc}. Building without amenity counts.")
        elements, osm_ok = [], False

    bboxes = {}
    for code, polys in raw.items():
        xs = [c[0] for p in polys for c in p[0]]
        ys = [c[1] for p in polys for c in p[0]]
        bboxes[code] = (min(xs), min(ys), max(xs), max(ys))

    counts = {code: dict.fromkeys(OSM_CATEGORIES, 0) for code in raw}
    unmatched = 0
    for el in elements:
        cat = osm_category(el.get("tags", {}))
        if not cat:
            continue
        lon = el.get("lon", el.get("center", {}).get("lon"))
        lat = el.get("lat", el.get("center", {}).get("lat"))
        if lon is None:
            continue
        for code, (x0, y0, x1, y1) in bboxes.items():
            if x0 <= lon <= x1 and y0 <= lat <= y1 and point_in_polygons(lon, lat, raw[code]):
                counts[code][cat] += 1
                break
        else:
            unmatched += 1
    print(f"  {len(elements)} OSM features, {unmatched} outside Utrecht's buurten")

    def current_values(code):
        """Newest available (value, year) per indicator for a CBS region code."""
        out = {}
        for ind in INDICATORS:
            for year in CURRENT_YEARS:
                row = editions[year].get(code)
                v = ind["fn"](row) if row else None
                if v is not None:
                    out[ind["key"]] = [round(v, 2), year]
                    break
        return out

    def series(code):
        """Yearly values for trend indicators; None where CBS left a year out."""
        out = {}
        for ind in INDICATORS:
            if not ind.get("trend"):
                continue
            vals = []
            for y in years:
                row = editions[y].get(code)
                v = ind["fn"](row) if row else None
                vals.append(None if v is None else round(v, 2))
            if sum(v is not None for v in vals) >= 2:
                out[ind["key"]] = vals
        return out

    def kids_count(code):
        for year in CURRENT_YEARS:
            row = editions[year].get(code)
            if row and row.get("k_0Tot15Jaar") is not None:
                return row["k_0Tot15Jaar"]
        return None

    def add_osm(values, c, kids):
        for cat, key in OSM_COUNT_KEYS.items():
            values[key] = [c[cat], "OSM"]
        area_km2 = (values.get("area", [0])[0] or 0) / 100
        if area_km2 > 0:
            values["o_stops"] = [round(c["stop"] / area_km2, 2), "OSM"]
        if kids and kids >= 50:
            values["o_playkids"] = [round(c["playground"] / kids * 1000, 2), "OSM"]

    buurten = []
    city_counts = dict.fromkeys(OSM_CATEGORIES, 0)
    for f in sorted(buurt_feats, key=lambda f: f["properties"]["buurtcode"]):
        p = f["properties"]
        code = p["buurtcode"]
        values = current_values(code)
        if osm_ok:
            add_osm(values, counts[code], kids_count(code))
            for k in city_counts:
                city_counts[k] += counts[code][k]
        buurten.append(dict(
            code=code,
            name=p["buurtnaam"],
            wijk=wijk_names.get(p["wijkcode"], ""),
            wijkcode=p["wijkcode"],
            postcode=(p.get("meest_voorkomende_postcode") or "").strip(),
            geom=project_geom(f["geometry"], tol=4),
            v=values,
            s=series(code),
        ))

    city = current_values(GEMEENTE)
    if osm_ok:
        add_osm(city, city_counts, kids_count(GEMEENTE))

    def public(ind, source):
        d = {k: v for k, v in ind.items() if k != "fn"}
        d.setdefault("unit", "")
        d.setdefault("unit_nl", d["unit"])
        d["group_nl"] = GROUP_NL[d["group"]]
        d["source"] = source
        return d

    indicators = [public(ind, CBS) for ind in INDICATORS]
    if osm_ok:
        indicators += [public(ind, OSM) for ind in OSM_INDICATORS]

    out = dict(
        meta=dict(
            generated=date.today().isoformat(),
            gemeente="Utrecht",
            cbs_tables=[{"table": CBS_TABLES[y], "year": y} for y in years],
            current_years=CURRENT_YEARS,
            series_years=years,
            boundaries=f"CBS wijk- en buurtkaart {PDOK_YEAR} via PDOK",
            osm=osm_ok,
        ),
        indicators=indicators,
        wijken=[{"code": f["properties"]["wijkcode"], "name": f["properties"]["wijknaam"],
                 "geom": project_geom(f["geometry"], tol=8)}
                for f in sorted(wijk_feats, key=lambda f: f["properties"]["wijkcode"])],
        city=city,
        city_series=series(GEMEENTE),
        buurten=buurten,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(buurten)} buurten, "
          f"{len(indicators)} indicators, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
