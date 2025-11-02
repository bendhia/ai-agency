import math
from typing import List, Dict, Any, Optional
import httpx

# Identify your app (use a real email if you can)
UA = {"User-Agent": "ai-agency/0.1 (you@example.com)", "Accept-Language": "en"}

# -------- Basic helpers --------
def haversine(a: float, b: float, c: float, d: float) -> float:
    R = 6_371_000
    p1, p2 = math.radians(a), math.radians(c)
    dphi = math.radians(c - a)
    dl = math.radians(d - b)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))

def _deg_box(lat: float, lng: float, radius_m: int = 2000) -> str:
    d = max(0.005, radius_m / 111_000)  # ≈ degrees for ~2km
    return f"{lng-d},{lat-d},{lng+d},{lat+d}"

def _short_name(full: str) -> str:
    return (full or "").split(",")[0].strip() or full

def _clamp_walk_minutes(distance_km: float, duration_min: int) -> int:
    # realistic lower bound ≈ 12 min per km
    min_by_pace = math.ceil(distance_km * 12)
    return max(duration_min, min_by_pace)

# -------- Tools the agent imports --------
async def nominatim_search(query: str, lat: Optional[float] = None, lng: Optional[float] = None,
                           limit: int = 3) -> List[Dict[str, Any]]:
    """Text search via OpenStreetMap Nominatim."""
    params = {"q": query, "format": "json", "limit": str(limit)}
    if lat is not None and lng is not None:
        params.update({"viewbox": _deg_box(lat, lng, 2000), "bounded": 1})

    async with httpx.AsyncClient(timeout=15, headers=UA) as cx:
        r = await cx.get("https://nominatim.openstreetmap.org/search", params=params)
        r.raise_for_status()
        data = r.json()

    return [{"name": d.get("display_name", ""), "lat": float(d["lat"]), "lng": float(d["lon"])} for d in data]

async def osrm_walking_time(o_lat: float, o_lng: float, d_lat: float, d_lng: float) -> Dict[str, Any]:
    """Walking route via public OSRM; falls back to haversine estimate."""
    url = f"https://router.project-osrm.org/route/v1/foot/{o_lng},{o_lat};{d_lng},{d_lat}"
    async with httpx.AsyncClient(timeout=15, headers=UA) as cx:
        r = await cx.get(url, params={"overview": "false"})
        if r.status_code == 200:
            j = r.json()
            routes = j.get("routes") or []
            if routes:
                dist = int(routes[0].get("distance", 0))
                dur = int(routes[0].get("duration", 0))
                if dist > 0 and dur > 0:
                    return {"distance_m": dist, "duration_s": dur, "source": "osrm"}

    meters = haversine(o_lat, o_lng, d_lat, d_lng)
    seconds = int(meters / (5000 / 3600))  # 5 km/h
    return {"distance_m": int(meters), "duration_s": seconds, "source": "haversine"}

# -------- Composite tool (search + walking time) --------
async def nearby_search_with_walk(query: str, origin_lat: float, origin_lng: float, limit: int = 3) -> Dict[str, Any]:
    """Search nearby places and include realistic walking distance/time for each."""
    q = "cafe" if "caf" in query.lower() else query  # prefer ASCII token for OSM
    places = await nominatim_search(q, origin_lat, origin_lng, limit=limit)

    cards = []
    for p in places:
        walk = await osrm_walking_time(origin_lat, origin_lng, p["lat"], p["lng"])
        distance_m = int(walk["distance_m"])
        duration_s = int(walk["duration_s"])

        distance_km = round(distance_m / 1000, 2)
        duration_min_raw = max(1, int(round(duration_s / 60)))
        duration_min = _clamp_walk_minutes(distance_km, duration_min_raw)

        cards.append({
            "name": _short_name(p["name"]),
            "lat": p["lat"],
            "lng": p["lng"],
            "distance_m": distance_m,
            "duration_s": duration_s,
            "distance_km": distance_km,
            "duration_min": duration_min,
            "map_url": f"https://www.openstreetmap.org/?mlat={p['lat']}&mlon={p['lng']}#map=15/{p['lat']}/{p['lng']}"
        })

    return {"cards": cards, "origin": {"lat": origin_lat, "lng": origin_lng}, "query": q}
