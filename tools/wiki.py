#cat > tools/wiki.py << 'EOF'
import httpx
UA = {"User-Agent": "ai-agent/0.1 (dev@example.com)"}

async def wikipedia_summary(title: str):
    t = title.replace(" ", "_")
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{t}"
    async with httpx.AsyncClient(timeout=12, headers=UA) as cx:
        r = await cx.get(url)
        if r.status_code == 200:
            j = r.json()
            return {
                "title": j.get("title"),
                "extract": j.get("extract"),
                "url": j.get("content_urls",{}).get("desktop",{}).get("page")
            }
    return {"title": title, "extract": None, "url": None}


