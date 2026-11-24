#!/usr/bin/env python3
"""
Recolector de anuncios de vivienda en Málaga -> data/listings.json

Fuentes (se activan en config.json):
  - idealista_api : API oficial de Idealista (requiere apikey/secret solicitados a Idealista)
  - apify         : último dataset de un actor/tarea de Apify (Fotocasa, Pisos.com, Idealista…)
  - import_dir    : ficheros .json / .csv que dejes en data/import/ (exportaciones manuales, otro proveedor…)

Uso:
  python collector.py            # recoge y escribe data/listings.json
  python collector.py --demo     # genera datos ficticios para probar el mapa

Programar cada 30 min (cron):
  */30 * * * * cd /ruta/malaga-mapa && /usr/bin/python3 collector.py >> collector.log 2>&1
"""
import base64, csv, glob, json, os, random, re, sys, time
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "listings.json")
CONFIG = os.path.join(HERE, "config.json")
MALAGA = (36.7213, -4.4214)

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def log(*a):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}]", *a, flush=True)

# ---------------------------------------------------------------- normalización
def num(v):
    """'245.000 €' -> 245000 ; '85 m²' -> 85 ; 3 -> 3"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return v
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if not s:
        return None
    if "," in s and "." in s:          # 1.234,56
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1 or re.search(r"\.\d{3}$", s):  # 245.000
        s = s.replace(".", "")
    else:
        s = s.replace(",", ".")
    try:
        f = float(s)
        return int(f) if f.is_integer() else f
    except ValueError:
        return None

def pick(d, *paths):
    """Primer valor no vacío entre varias rutas tipo 'a.b.0.c'."""
    for p in paths:
        cur = d
        for part in p.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                cur = None
            if cur is None:
                break
        if cur not in (None, "", [], {}):
            return cur
    return None

def norm_operation(v):
    s = str(v or "").lower()
    return "rent" if any(k in s for k in ("rent", "alquil", "renta")) else "sale"

def norm_type(v):
    s = str(v or "").lower()
    if any(k in s for k in ("penthouse", "atico", "ático")): return "penthouse"
    if "duplex" in s or "dúplex" in s: return "duplex"
    if any(k in s for k in ("studio", "estudio", "loft")): return "studio"
    if any(k in s for k in ("chalet", "casa", "house", "villa", "adosad", "unifamiliar", "countryhouse")): return "house"
    if any(k in s for k in ("flat", "piso", "apartament", "apartment", "home", "vivienda", "planta", "bajo")): return "flat"
    return "other" if s else "flat"

FEATURE_KEYS = {
    "lift": ("hasLift", "lift", "elevator", "ascensor"),
    "terrace": ("hasTerrace", "terrace", "terraza"),
    "parking": ("parkingSpace.hasParkingSpace", "hasParking", "parking", "garage", "garaje"),
    "pool": ("hasSwimmingPool", "swimmingPool", "pool", "piscina"),
    "ac": ("hasAirConditioning", "airConditioning", "aireAcondicionado"),
}

def features_of(raw):
    out = []
    blob = json.dumps(raw, ensure_ascii=False).lower()
    for key, paths in FEATURE_KEYS.items():
        v = pick(raw, *paths)
        if v is True or (isinstance(v, str) and v.lower() in ("true", "si", "sí", "yes")):
            out.append(key)
        elif v is None and isinstance(raw.get("features"), list):
            if any(p.lower() in blob for p in paths[-2:]):
                out.append(key)
    return out

def normalize(raw, portal):
    """Convierte un anuncio de cualquier fuente al esquema común del mapa."""
    url = pick(raw, "url", "link", "detailUrl", "propertyUrl", "href")
    if url and url.startswith("/"):
        base = {"idealista": "https://www.idealista.com", "fotocasa": "https://www.fotocasa.es",
                "pisoscom": "https://www.pisos.com"}.get(portal, "")
        url = base + url
    rid = pick(raw, "propertyCode", "id", "adId", "propertyId", "listingId", "realEstateId") or url
    if not rid:
        return None
    addr = pick(raw, "address", "direccion", "dirección", "location.address", "address.street", "street", "title")
    if isinstance(addr, dict):
        addr = " ".join(str(x) for x in addr.values() if isinstance(x, (str, int)))
    lat = num(pick(raw, "latitude", "lat", "latitud", "coordinates.latitude", "location.latitude", "location.lat", "address.latitude", "geo.lat"))
    lng = num(pick(raw, "longitude", "lng", "lon", "longitud", "coordinates.longitude", "location.longitude", "location.lng", "address.longitude", "geo.lng", "geo.lon"))
    if lat is not None and lng is not None and not (35.5 < lat < 37.5 and -5.8 < lng < -3.5):
        lat = lng = None  # coordenadas fuera de la provincia: descartar
    item = {
        "id": f"{portal}:{rid}",
        "portal": portal,
        "url": url,
        "title": pick(raw, "title", "suggestedTexts.title", "headline", "name") or "",
        "operation": norm_operation(pick(raw, "operation", "operacion", "operación", "transactionType", "transaction", "operationType")),
        "type": norm_type(pick(raw, "detailedType.subTypology", "propertyType", "tipo", "typology", "buildingSubtype", "type", "subtype")),
        "price": num(pick(raw, "price", "precio", "priceInfo.price.amount", "price.amount", "rawPrice", "priceValue")),
        "size": num(pick(raw, "size", "surface", "superficie", "metros", "area", "m2", "squareMeters", "features.size", "constructedArea")),
        "rooms": num(pick(raw, "rooms", "bedrooms", "habitaciones", "features.rooms", "numRooms")),
        "bathrooms": num(pick(raw, "bathrooms", "baths", "banos", "baños", "features.bathrooms", "numBathrooms")),
        "floor": str(pick(raw, "floor", "planta") or "") or None,
        "address": addr or "",
        "district": pick(raw, "district", "distrito", "barrio", "address.district", "location.district", "zone") or "",
        "neighborhood": pick(raw, "neighborhood", "address.neighborhood", "location.neighborhood") or "",
        "lat": lat, "lng": lng,
        "image": pick(raw, "thumbnail", "mainImage", "image", "imagen", "foto", "images.0.url", "images.0", "multimedia.images.0.url", "photos.0"),
        "published": pick(raw, "publicationDate", "date", "publishedAt", "modificationDate"),
        "features": features_of(raw),
    }
    if not isinstance(item["image"], str):
        item["image"] = None
    return item

# ---------------------------------------------------------------- fuentes
def http():
    if requests is None:
        sys.exit("Falta 'requests': pip install requests")
    s = requests.Session()
    s.headers["User-Agent"] = "malaga-mapa-collector/1.0"
    return s

def src_idealista_api(cfg):
    """API oficial: https://developers.idealista.com  (cuota gratuita pequeña: cuida max_pages)."""
    key = os.environ.get("IDEALISTA_API_KEY") or cfg.get("api_key")
    secret = os.environ.get("IDEALISTA_API_SECRET") or cfg.get("api_secret")
    if not key or not secret:
        log("idealista_api: sin credenciales, se omite"); return []
    s = http()
    tok = s.post("https://api.idealista.com/oauth/token",
                 headers={"Authorization": "Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode()},
                 data={"grant_type": "client_credentials", "scope": "read"}, timeout=30)
    tok.raise_for_status()
    s.headers["Authorization"] = "Bearer " + tok.json()["access_token"]
    out = []
    for op in cfg.get("operations", ["sale", "rent"]):
        for page in range(1, int(cfg.get("max_pages", 2)) + 1):
            r = s.post("https://api.idealista.com/3.5/es/search", data={
                "center": f"{cfg.get('lat', MALAGA[0])},{cfg.get('lng', MALAGA[1])}",
                "distance": cfg.get("distance_m", 8000), "propertyType": "homes", "operation": op,
                "maxItems": 50, "numPage": page, "order": "publicationDate", "sort": "desc",
                "country": "es", "locale": "es"}, timeout=30)
            r.raise_for_status()
            j = r.json()
            out += [normalize({**e, "operation": op}, "idealista") for e in j.get("elementList", [])]
            if page >= j.get("totalPages", 1):
                break
            time.sleep(1.2)
    return out

def src_apify(cfg):
    """Lee el dataset de la última ejecución correcta de un actor o tarea de Apify.
    Programa el actor en Apify (Schedules) y este script solo recoge el resultado."""
    token = os.environ.get("APIFY_TOKEN") or cfg.get("token")
    if not token:
        log("apify: sin token, se omite"); return []
    s = http()
    out = []
    for job in cfg.get("jobs", []):
        if "USUARIO/" in (job.get("actor") or "") and not job.get("task"):
            continue  # ejemplo sin rellenar
        kind = "actor-tasks" if job.get("task") else "acts"
        ident = (job.get("task") or job["actor"]).replace("/", "~")
        url = f"https://api.apify.com/v2/{kind}/{ident}/runs/last/dataset/items"
        r = s.get(url, params={"token": token, "status": "SUCCEEDED", "clean": "true", "format": "json"}, timeout=120)
        if r.status_code != 200:
            log(f"apify {ident}: HTTP {r.status_code}"); continue
        items = r.json()
        op = job.get("operation")  # por si el actor no devuelve la operación
        out += [normalize({**({"operation": op} if op else {}), **it}, job["portal"]) for it in items]
        log(f"apify {ident} ({job['portal']}): {len(items)} anuncios")
    return out

def src_import_dir(cfg):
    """data/import/<portal>__loquesea.json|csv  (el prefijo antes de '__' indica el portal)."""
    folder = os.path.join(HERE, cfg.get("path", "data/import"))
    out = []
    for f in sorted(glob.glob(os.path.join(folder, "*"))):
        name = os.path.basename(f)
        portal = name.split("__")[0].lower() if "__" in name else cfg.get("default_portal", "otro")
        portal = {"pisos": "pisoscom", "pisos.com": "pisoscom"}.get(portal, portal)
        try:
            if f.endswith(".json"):
                data = json.load(open(f, encoding="utf-8"))
                rows = data.get("listings", data.get("items", [])) if isinstance(data, dict) else data
            elif f.endswith(".csv"):
                sample = open(f, encoding="utf-8-sig").read(2048)
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
                rows = list(csv.DictReader(open(f, encoding="utf-8-sig"), dialect=dialect))
            else:
                continue
        except Exception as e:
            log(f"import {name}: error {e}"); continue
        out += [normalize(r, portal) for r in rows]
        log(f"import {name} ({portal}): {len(rows)} filas")
    return out

SOURCES = {"idealista_api": src_idealista_api, "apify": src_apify, "import_dir": src_import_dir}

# ---------------------------------------------------------------- merge
def dedupe(items):
    """Un mismo piso publicado en varios portales se agrupa: queda uno y el resto en 'also_on'."""
    groups = {}
    for it in items:
        if it["lat"] is not None and it["price"] and it["size"]:
            k = (round(it["lat"], 3), round(it["lng"], 3), it["operation"], round(it["price"], -3), round(it["size"] / 5))
        else:
            k = ("id", it["id"])
        groups.setdefault(k, []).append(it)
    out = []
    for g in groups.values():
        main = g[0]
        others = {x["portal"]: x for x in g[1:] if x["portal"] != main["portal"]}
        if others:
            main["also_on"] = [{"portal": p, "url": x["url"], "id": x["id"]} for p, x in others.items()]
        out.append(main)
    return out

def collect():
    cfg = json.load(open(CONFIG, encoding="utf-8")) if os.path.exists(CONFIG) else {}
    prev = {}
    if os.path.exists(OUT):
        try:
            for l in json.load(open(OUT, encoding="utf-8")).get("listings", []):
                prev[l["id"]] = l.get("first_seen")
                for a in l.get("also_on", []):
                    prev[a["id"]] = l.get("first_seen")
        except Exception:
            pass
    items, counts = [], {}
    for name, fn in SOURCES.items():
        scfg = cfg.get(name, {})
        auto = {"idealista_api": bool(os.environ.get("IDEALISTA_API_KEY") and os.environ.get("IDEALISTA_API_SECRET")),
                "apify": bool(os.environ.get("APIFY_TOKEN"))}.get(name, False)
        if not (scfg.get("enabled") or auto):
            continue
        try:
            got = [x for x in fn(scfg) if x]
            items += got
            counts[name] = len(got)
        except Exception as e:
            log(f"{name}: ERROR {e}")
            counts[name] = f"error: {e}"
    # quedarse con la última versión de cada id
    items = list({it["id"]: it for it in items}.values())
    ts = now_iso()
    for it in items:
        it["first_seen"] = prev.get(it["id"]) or ts
    listings = dedupe(items)
    if not listings:
        prev_is_real = False
        try:
            prev_is_real = bool(prev) and not json.load(open(OUT, encoding="utf-8")).get("demo")
        except Exception:
            pass
        if prev_is_real:
            log("Ninguna fuente devolvió datos: se conserva el fichero anterior"); return
        log("Sin fuentes reales con datos todavía -> se generan datos DEMO")
        return demo()
    write({"generated_at": ts, "demo": False, "sources": counts, "listings": listings})
    new = sum(1 for l in listings if l["first_seen"] == ts)
    log(f"OK: {len(listings)} anuncios activos ({new} nuevos) -> {OUT}")

def write(payload):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)

# ---------------------------------------------------------------- demo
DISTRICTS = {
    "Centro": (36.7213, -4.4214), "Este": (36.7210, -4.3750), "Ciudad Jardín": (36.7420, -4.4230),
    "Bailén-Miraflores": (36.7345, -4.4380), "Palma-Palmilla": (36.7470, -4.4370),
    "Cruz de Humilladero": (36.7160, -4.4460), "Carretera de Cádiz": (36.7000, -4.4470),
    "Churriana": (36.6660, -4.4990), "Campanillas": (36.7290, -4.5390),
    "Puerto de la Torre": (36.7410, -4.4890), "Teatinos-Universidad": (36.7210, -4.4740),
}
STREETS = ["Calle Larios", "Av. de Andalucía", "Paseo Marítimo", "Calle Carretería", "Av. Juan Sebastián Elcano",
           "Calle Victoria", "Av. Velázquez", "Calle Héroe de Sostoa", "Camino de Suárez", "Av. Plutarco"]

def demo(n=260, seed=None):
    """Datos FICTICIOS para probar la interfaz. Cada ejecución añade y quita algunos anuncios."""
    rnd = random.Random(seed)
    prev = []
    if os.path.exists(OUT):
        try:
            d = json.load(open(OUT, encoding="utf-8"))
            if d.get("demo"): prev = d["listings"]
        except Exception: pass
    ts = now_iso()
    def make(i):
        dname, (la, ln) = rnd.choice(list(DISTRICTS.items()))
        op = "rent" if rnd.random() < 0.38 else "sale"
        typ = rnd.choices(["flat", "penthouse", "duplex", "studio", "house"], [60, 10, 8, 10, 12])[0]
        size = {"studio": rnd.randint(28, 45), "house": rnd.randint(110, 320)}.get(typ, rnd.randint(50, 150))
        rooms = 0 if typ == "studio" else max(1, min(6, round(size / 32 + rnd.uniform(-1, 1))))
        base = {"Centro": 4600, "Este": 4300, "Teatinos-Universidad": 3200, "Churriana": 2400, "Campanillas": 1900}.get(dname, 2700)
        ppm = base * rnd.uniform(.75, 1.3) * (1.15 if typ == "penthouse" else 1)
        price = round(size * ppm, -3) if op == "sale" else round(size * ppm / 230, -1)
        portal = rnd.choices(["idealista", "fotocasa", "pisoscom"], [45, 35, 20])[0]
        return {
            "id": f"{portal}:demo{rnd.randint(10**7, 10**8)}", "portal": portal, "url": None,
            "title": f"{ {'flat':'Piso','penthouse':'Ático','duplex':'Dúplex','studio':'Estudio','house':'Chalet'}[typ]} en {dname} (DEMO)",
            "operation": op, "type": typ, "price": price, "size": size, "rooms": rooms,
            "bathrooms": max(1, min(4, rooms // 2 + rnd.randint(0, 1))), "floor": f"{rnd.randint(0, 9)}ª planta" if typ != "house" else None,
            "address": f"{rnd.choice(STREETS)}, {rnd.randint(1, 120)} (ficticio)", "district": dname, "neighborhood": "",
            "lat": round(la + rnd.gauss(0, .007), 6), "lng": round(ln + rnd.gauss(0, .009), 6), "image": None,
            "published": (datetime.now(timezone.utc) - timedelta(days=rnd.randint(0, 40))).date().isoformat(),
            "features": [f for f, p in (("lift", .6), ("terrace", .4), ("parking", .3), ("pool", .2), ("ac", .5)) if rnd.random() < p],
            "first_seen": ts,
        }
    if prev:
        keep = [l for l in prev if rnd.random() > 0.05]                 # ~5 % se retiran
        listings = keep + [make(i) for i in range(rnd.randint(5, 15))]   # entran unos cuantos nuevos
    else:
        old = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0).isoformat()
        listings = [dict(make(i), first_seen=old) for i in range(n)]
    write({"generated_at": ts, "demo": True, "sources": {"demo": len(listings)}, "listings": listings})
    log(f"DEMO: {len(listings)} anuncios ficticios -> {OUT}")

if __name__ == "__main__":
    demo() if "--demo" in sys.argv else collect()
