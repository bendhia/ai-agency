# tools/travel.py
import httpx, datetime as dt, math
from typing import List, Dict, Any, Optional, Tuple

OSRM_BASE = "https://router.project-osrm.org"
NOM_BASE  = "https://nominatim.openstreetmap.org/search"
OVERPASS  = "https://overpass-api.de/api/interpreter"

HEADERS = {
    "User-Agent": "ai-agency-travel-agent/2.2 (contact: example@example.com)",
    "Accept-Language": "en,tr"
}

# ---- tuning knobs ----
MIN_WALK_MIN = 3              # never show 0 min for walking
WALK_SPEED_MIN_PER_KM = 12    # ~5 km/h heuristic when clamping

# --- interest expansions & OSM tag mapping ---
INTEREST_EXPANSIONS = {
    "food": ["restaurants", "cafe", "cafes", "coffee", "bakery", "street food", "kebab", "lokanta", "meze"],
    "cafes": ["cafe", "cafes", "coffee", "coffee shop"],
    "history": ["historical sites", "museums", "mosque", "palace", "basilica", "archaeology"],
    "museums": ["museums", "art museum"],
    "landmarks": ["landmarks", "monuments", "viewpoints"],
    "parks": ["parks", "gardens", "promenade"],
}
DEFAULT_INTERESTS = ["landmarks", "museums", "cafes", "parks"]

# OSM tags per coarse interest bucket
OSM_TAGS = {
    "food": [
        ('amenity', 'restaurant'),
        ('amenity', 'cafe'),
        ('amenity', 'fast_food'),
        ('amenity', 'food_court'),
        ('shop', 'bakery'),
    ],
    "cafes": [
        ('amenity', 'cafe'),
        ('amenity', 'coffee_shop'),
    ],
    "museums": [
        ('tourism', 'museum'),
    ],
    "history": [
        ('tourism', 'museum'),
        ('historic', '*'),
        ('amenity', 'place_of_worship'),
    ],
    "landmarks": [
        ('tourism', 'attraction'),
        ('tourism', 'viewpoint'),
        ('historic', '*'),
        ('man_made', 'tower'),
    ],
    "parks": [
        ('leisure', 'park'),
        ('leisure', 'garden'),
    ],
}

BUCKET_OF_TERM = {
    "restaurant": "food", "restaurants": "food", "bakery": "food", "kebab": "food", "meze": "food", "street food": "food", "lokanta": "food",
    "cafe": "cafes", "cafes": "cafes", "coffee": "cafes", "coffee shop": "cafes",
    "museum": "museums", "museums": "museums", "art museum": "museums",
    "historic": "history", "historical sites": "history", "mosque": "history", "palace": "history", "basilica": "history", "archaeology": "history",
    "landmarks": "landmarks", "monuments": "landmarks", "viewpoints": "landmarks", "viewpoint": "landmarks", "tower": "landmarks",
    "parks": "parks", "park": "parks", "gardens": "parks", "promenade": "parks",
}

def _expand_terms(interests: List[str]) -> List[str]:
    out: List[str] = []
    for term in interests or []:
        t = (term or "").strip().lower()
        out.extend(INTEREST_EXPANSIONS.get(t, [t]))
    seen = set(); uniq: List[str] = []
    for t in out:
        if t and t not in seen:
            seen.add(t); uniq.append(t)
    return uniq or DEFAULT_INTERESTS

# ---------- helpers ----------
def _haversine_km(a_lat, a_lng, b_lat, b_lng) -> float:
    R = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, [a_lat, a_lng, b_lat, b_lng])
    dla, dlo = la2 - la1, lo2 - lo1
    x = math.sin(dla/2)**2 + math.cos(la1)*math.cos(la2)*math.sin(dlo/2)**2
    return 2 * R * math.asin(math.sqrt(x))

async def _city_center(destination: str) -> Optional[Dict[str, float]]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            NOM_BASE,
            params={"q": destination, "format": "json", "limit": 1, "addressdetails": 1},
            headers=HEADERS,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        return {"lat": float(data[0]["lat"]), "lng": float(data[0]["lon"])}

async def _nominatim_search(query: str, lat=None, lng=None, limit=40) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15) as client:
        params = {"q": query, "format": "json", "limit": limit}
        if lat is not None and lng is not None:
            params["lat"], params["lon"] = lat, lng
        r = await client.get(NOM_BASE, params=params, headers=HEADERS)
        r.raise_for_status()
        out = []
        for x in r.json():
            la, lo = float(x["lat"]), float(x["lon"])
            out.append({
                "name": x.get("display_name", "").strip() or "Unnamed place",
                "lat": la,
                "lng": lo,
                "map_url": f"https://www.openstreetmap.org/?mlat={la}&mlon={lo}#map=15/{la}/{lo}"
            })
        return out

def _overpass_query(tags: List[Tuple[str,str]], lat: float, lng: float, radius_m: int) -> str:
    parts = []
    for k, v in tags:
        if v == "*":
            parts.append(f'node["{k}"](around:{radius_m},{lat},{lng});way["{k}"](around:{radius_m},{lat},{lng});rel["{k}"](around:{radius_m},{lat},{lng});')
        else:
            parts.append(f'node["{k}"="{v}"](around:{radius_m},{lat},{lng});way["{k}"="{v}"](around:{radius_m},{lat},{lng});rel["{k}"="{v}"](around:{radius_m},{lat},{lng});')
    body = "".join(parts)
    return f'[out:json][timeout:25];({body});out center 120;'

async def _overpass_search(tags: List[Tuple[str,str]], lat: float, lng: float, radius_km: float = 6.0) -> List[Dict[str, Any]]:
    q = _overpass_query(tags, lat, lng, int(radius_km * 1000))
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        r = await client.post(OVERPASS, data={"data": q})
        r.raise_for_status()
        js = r.json()
    out: List[Dict[str, Any]] = []
    for el in js.get("elements", []):
        if el.get("type") == "node":
            la, lo = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            la, lo = c.get("lat"), c.get("lon")
        if la is None or lo is None:
            continue
        tags = el.get("tags", {}) or {}
        name = tags.get("name") or tags.get("name:en") or "Unnamed place"
        out.append({
            "name": name.strip(),
            "lat": float(la),
            "lng": float(lo),
            "map_url": f"https://www.openstreetmap.org/?mlat={la}&mlon={lo}#map=15/{la}/{lo}",
            "_tags": tags,
        })
    return out

async def _osrm_time(o_lat, o_lng, d_lat, d_lng, mode="foot") -> Dict[str, Optional[float]]:
    profile = {"foot": "walking", "bike": "cycling", "driving": "driving"}.get(mode, "walking")
    url = f"{OSRM_BASE}/route/v1/{profile}/{o_lng},{o_lat};{d_lng},{d_lat}"
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
        r = await client.get(url, params={"overview": "false"})
        r.raise_for_status()
        js = r.json()
        if not js.get("routes"):
            return {"distance_km": None, "duration_min": None}
        route = js["routes"][0]
        return {
            "distance_km": round(route["distance"] / 1000, 2),
            "duration_min": int(round(route["duration"] / 60)),
        }

def _suspicious_foot(distance_km: Optional[float], duration_min: Optional[float]) -> bool:
    if not distance_km or not duration_min or duration_min <= 0:
        return False
    speed_kmh = 60.0 * (distance_km / duration_min)
    return speed_kmh > 8.0

def _fallback_walk_minutes(distance_km: float) -> int:
    return int(round(distance_km * WALK_SPEED_MIN_PER_KM))

def _norm_name(s: str) -> str:
    s = (s or "").lower().strip()
    return s.split(",")[0].strip()

def _bucket_for_tags(tags: Dict[str, str]) -> str:
    if tags.get("amenity") in ("restaurant","fast_food","food_court"): return "food"
    if tags.get("amenity") == "cafe": return "cafes"
    if tags.get("shop") == "bakery": return "food"
    if tags.get("tourism") == "museum": return "museums"
    if tags.get("historic"): return "history"
    if tags.get("tourism") in ("attraction","viewpoint"): return "landmarks"
    if tags.get("man_made") == "tower": return "landmarks"
    if tags.get("leisure") in ("park","garden"): return "parks"
    return "landmarks"

def _diversify(items: List[Dict[str, Any]], per_day: int) -> List[Dict[str, Any]]:
    order = ["history","landmarks","museums","food","cafes","parks"]
    buckets: Dict[str, List[Dict[str, Any]]] = {b: [] for b in order}
    other: List[Dict[str, Any]] = []
    for it in items:
        b = it.get("_bucket")
        if b in buckets:
            buckets[b].append(it)
        else:
            other.append(it)
    out: List[Dict[str, Any]] = []
    while (len(out) < per_day) and (any(buckets.values()) or other):
        for b in order:
            if len(out) >= per_day: break
            if buckets[b]:
                out.append(buckets[b].pop(0))
        if len(out) < per_day and other:
            out.append(other.pop(0))
        if len(out) >= per_day: break
        if not any(buckets.values()) and other:
            while other and len(out) < per_day:
                out.append(other.pop(0))
    return out

def _parse_date(s: Optional[str]) -> Optional[dt.date]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None

def _future_default_range() -> (dt.date, dt.date):
    start = dt.date.today() + dt.timedelta(days=7)
    end   = start + dt.timedelta(days=2)
    return start, end

def _date_range(d0: dt.date, d1: dt.date) -> List[dt.date]:
    n = (d1 - d0).days + 1
    return [d0 + dt.timedelta(days=i) for i in range(n)]

def _deeplink_flights(origin_city: str, dest_city: str, depart: Optional[dt.date], return_: Optional[dt.date]) -> str:
    base = "https://www.google.com/travel/flights"
    q = f"?q=Flights%20to%20{dest_city.replace(' ','%20')}"
    if origin_city:
        q = f"?q=Flights%20from%20{origin_city.replace(' ','%20')}%20to%20{dest_city.replace(' ','%20')}"
    if depart:
        q += f"%20on%20{depart.isoformat()}"
    if return_:
        q += f"%20return%20{return_.isoformat()}"
    return base + q

def _deeplink_hotels(city: str, checkin: Optional[dt.date], checkout: Optional[dt.date]) -> str:
    base = "https://www.booking.com/searchresults.html"
    params = f"?ss={city.replace(' ','+')}"
    if checkin:
        params += f"&checkin_year={checkin.year}&checkin_month={checkin.month}&checkin_monthday={checkin.day}"
    if checkout:
        params += f"&checkout_year={checkout.year}&checkout_month={checkout.month}&checkout_monthday={checkout.day}"
    return base + params

# ---------- main planner ----------
async def plan_trip(
    destination: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    interests: Optional[List[str]] = None,
    pace: str = "moderate",
    mode: str = "foot",
    origin_lat: Optional[float] = None,
    origin_lng: Optional[float] = None,
    limit_per_day: int = 5,
    radius_km: float = 12.0,
) -> Dict[str, Any]:
    """
    Day-by-day itinerary around the destination.
    If origin is provided, search & filter around origin; else around city center.
    Overpass for category-accurate POIs, Nominatim as fallback. Diversifies days and de-dups globally.
    """
    # Dates
    d0 = _parse_date(start_date)
    d1 = _parse_date(end_date)
    if not d0 or not d1 or d1 < d0:
        d0, d1 = _future_default_range()
    days = _date_range(d0, d1)

    # Interests → expanded
    terms = _expand_terms(interests or DEFAULT_INTERESTS)

    # Center: prefer origin if provided
    center = {"lat": origin_lat, "lng": origin_lng} if (origin_lat is not None and origin_lng is not None) else await _city_center(destination)
    if not center:
        return {
            "destination": destination,
            "start_date": d0.isoformat(),
            "end_date": d1.isoformat(),
            "mode": mode,
            "itinerary": [],
            "links": {
                "flights": _deeplink_flights("", destination, d0, d1),
                "hotels": _deeplink_hotels(destination, d0, d1),
            },
            "notes": "Could not locate the city center for your destination.",
        }
    c_lat, c_lng = float(center["lat"]), float(center["lng"])

    # Choose Overpass buckets
    buckets = set()
    for t in interests or DEFAULT_INTERESTS:
        t = (t or "").strip().lower()
        if t in OSM_TAGS:
            buckets.add(t)
        else:
            b = BUCKET_OF_TERM.get(t)
            if b: buckets.add(b)
    if not buckets:
        buckets = {"landmarks","museums","cafes","food","parks"}

    tag_list: List[Tuple[str,str]] = []
    for b in buckets:
        tag_list.extend(OSM_TAGS.get(b, []))

    # Fetch with Overpass (around chosen center)
    raw: List[Dict[str, Any]] = await _overpass_search(tag_list, c_lat, c_lng, radius_km=min(radius_km, 12.0))
    # If still too few, backfill with Nominatim sweeps
    if len(raw) < 12:
        for q in ["museum in " + destination, "restaurant in " + destination, "cafe in " + destination, "park in " + destination]:
            raw += await _nominatim_search(q, lat=c_lat, lng=c_lng, limit=30)

    # De-dup by normalized name (keep closest to center)
    best_by_name: Dict[str, Dict[str, Any]] = {}
    for p in raw:
        key = _norm_name(p.get("name", ""))
        if not key:
            continue
        d_center = _haversine_km(c_lat, c_lng, p["lat"], p["lng"])
        p["_d_center_km"] = d_center
        if key not in best_by_name or d_center < best_by_name[key]["_d_center_km"]:
            best_by_name[key] = p
    pois = list(best_by_name.values())
    pois.sort(key=lambda x: round(x["_d_center_km"], 3))

    # Compute distance/time from ORIGIN if provided; else estimate from center
    enriched: List[Dict[str, Any]] = []
    for p in pois:
        bucket = _bucket_for_tags(p.get("_tags", {}))
        entry = {
            "name": p["name"].split(",")[0],
            "lat": p["lat"],
            "lng": p["lng"],
            "map_url": p["map_url"],
            "distance_km": None,
            "duration_min": None,
            "_bucket": bucket,
        }
        if origin_lat is not None and origin_lng is not None:
            trip = await _osrm_time(origin_lat, origin_lng, p["lat"], p["lng"], mode)
            if mode == "foot" and _suspicious_foot(trip["distance_km"], trip["duration_min"]):
                trip["duration_min"] = _fallback_walk_minutes(trip["distance_km"] or 0.0)
            if mode == "foot" and trip["duration_min"] is not None:
                trip["duration_min"] = max(MIN_WALK_MIN, trip["duration_min"])
            entry["distance_km"] = trip["distance_km"]
            entry["duration_min"] = trip["duration_min"]
        else:
            dkm = p["_d_center_km"]
            entry["distance_km"] = round(dkm, 2)
            if mode == "foot":
                mins = _fallback_walk_minutes(dkm)
                entry["duration_min"] = max(MIN_WALK_MIN, mins)
            elif mode == "bike":
                entry["duration_min"] = int(round(dkm * 4))
            else:
                entry["duration_min"] = int(round(dkm * 2.5))
        enriched.append(entry)

    # If origin provided, filter too far for the chosen mode
    if origin_lat is not None and origin_lng is not None:
        if mode == "foot":   max_km = 6.0
        elif mode == "bike": max_km = 12.0
        else:                max_km = 30.0
        enriched = [e for e in enriched if (e["distance_km"] or 0) <= max_km]
        if not enriched:
            # last resort: keep 20 nearest by straight-line
            enriched = sorted(
                [{"name": p["name"].split(",")[0], "lat": p["lat"], "lng": p["lng"], "map_url": p["map_url"],
                  "distance_km": round(_haversine_km(origin_lat, origin_lng, p["lat"], p["lng"]), 2),
                  "duration_min": None, "_bucket": _bucket_for_tags(p.get("_tags", {}))} for p in pois],
                key=lambda x: x["distance_km"]
            )[:20]

    # ---------------- Distribute across days with diversification + global de-dup ----------------
    plan: List[Dict[str, Any]] = []
    per_day = max(1, int(limit_per_day))
    used_keys = set()

    def _key(e):  # stable key across days
        return (e["name"].lower(), round(e["lat"],6), round(e["lng"],6))

    # start with a clean pool (no pre-used items)
    pool = []
    for e in enriched:
        if _key(e) not in used_keys:
            pool.append(e)

    while days and pool:
        day = days.pop(0)
        # look ahead a bit to diversify better
        lookahead = pool[: per_day * 4]
        diversified = _diversify(lookahead, per_day)

        # take only items not used yet
        day_items = []
        picked_keys = set()
        for it in diversified:
            k = _key(it)
            if k in used_keys or k in picked_keys:
                continue
            picked_keys.add(k)
            # strip helper fields
            it = dict(it)
            it.pop("_bucket", None)
            day_items.append(it)
            if len(day_items) >= per_day:
                break

        if day_items:
            plan.append({"date": day.isoformat(), "items": day_items})

        # rebuild pool excluding used
        used_keys.update(picked_keys)
        pool = [e for e in pool if _key(e) not in used_keys]

    flights = _deeplink_flights("", destination, d0, d1)
    hotels  = _deeplink_hotels(destination, d0, d1)

    return {
        "destination": destination,
        "start_date": d0.isoformat(),
        "end_date":   d1.isoformat(),
        "mode": mode,
        "itinerary": plan,
        "links": {"flights": flights, "hotels": hotels},
        "notes": "POIs from Overpass + Nominatim fallback; diversified days; realistic walking times; global de-dup across days.",
    }
