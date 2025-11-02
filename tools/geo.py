import httpx, math
from typing import List, Dict, Any, Optional

# 💡 Put a real email here so Nominatim can contact you if needed
UA = {"User-Agent": "ai-agent/0.1 (youremail@example.com)", "Accept-Language": "en"}

def _deg_box(lat: float, lng: float, radius_m: int = 2000):
    # ~1 deg ≈ 111km → 2km ≈ 0.018 deg
    d = max(0.005, radius_m / 111_000)  # keep a minimum box
    return f"{lng-d},{lat-d},{lng+d},{lat+d}"

async def nominatim_search(query: str, lat: Optional[float] = None, lng: Optional[float] = None, limit: int = 3) -> List[Dict[str, Any]]:
    params = {"q": query, "format": "json", "limit": str(limit)}
    if lat is not None and lng is not None:
        params.update({"viewbox": _deg_box(lat, lng, 2000), "bounded": 1})

    async with httpx.AsyncClient(timeout=15, headers=UA) as cx:
        r = await cx.get("https://nominatim.openstreetmap.org/search", params=params)
        r.raise_for_status()
        data = r.json()

    results = [{"name": d.get("display_name",""), "lat": float(d["lat"]), "lng": float(d["lon"])} for d in data]
    if results:
        return results

    # Fallbacks: switch to common tokens likely in OSM
    fallback = "cafe" if query.lower().startswith("caf") or "coffee" in query.lower() else f"{query} cafe"
    if fallback != query:
        params["q"] = fallback
        async with httpx.AsyncClient(timeout=15, headers=UA) as cx:
            r = await cx.get("https://nominatim.openstreetmap.org/search", params=params)
            r.raise_for_status()
            data = r.json()
        return [{"name": d.get("display_name",""), "lat": float(d["lat"]), "lng": float(d["lon"])} for d in data]

    return results

async def osrm_walking_time(o_lat: float, o_lng: float, d_lat: float, d_lng: float):
    url = f"https://router.project-osrm.org/route/v1/foot/{o_lng},{o_lat};{d_lng},{d_lat}"
    async with httpx.AsyncClient(timeout=15, headers=UA) as cx:
        r = await cx.get(url, params={"overview":"false"})
        if r.status_code == 200 and (r.json().get("routes") or []):
            route = r.json()["routes"][0]
            return {"distance_m": int(route["distance"]), "duration_s": int(route["duration"]), "source": "osrm"}
    meters = haversine(o_lat,o_lng,d_lat,d_lng)
    return {"distance_m": int(meters), "duration_s": int(meters/(5000/3600)), "source": "haversine"}

def haversine(a,b,c,d):
    R=6371000
    p1,p2=math.radians(a),math.radians(c)
    dphi=math.radians(c-a); dl=math.radians(d-b)
    h=math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(h))

async def nearby_search_with_walk(query: str, origin_lat: float, origin_lng: float, limit: int = 3):
    """Search nearby places and include walking distance/time for each."""
    places = await nominatim_search(query, origin_lat, origin_lng, limit=limit)
    enriched = []
    for p in places:
        walk = await osrm_walking_time(origin_lat, origin_lng, p["lat"], p["lng"])
        enriched.append({
            "name": p["name"],
            "lat": p["lat"],
            "lng": p["lng"],
            "distance_m": walk["distance_m"],
            "duration_s": walk["duration_s"],
            "map_url": f"https://www.openstreetmap.org/?mlat={p['lat']}&mlon={p['lng']}#map=15/{p['lat']}/{p['lng']}"
        })
    return enriched
