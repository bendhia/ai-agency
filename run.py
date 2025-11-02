import os, asyncio, json, argparse
from dotenv import load_dotenv
from agent import run_agent

load_dotenv()

def build_prompt(args):
    q     = args.q or "cafes"
    lat   = args.lat or float(os.getenv("DEFAULT_LAT", "36.7529"))
    lng   = args.lng or float(os.getenv("DEFAULT_LNG", "3.0420"))
    limit = args.limit
    # steer the model to composite tool
    base = f"Use nearby_search_with_walk to find {limit} {q} near {lat}, {lng} and include walking time."
    return base

async def main():
    ap = argparse.ArgumentParser(description="Run the travel agent from CLI")
    ap.add_argument("--q", help="What to search (e.g. cafes, pizza, museum)")
    ap.add_argument("--lat", type=float, help="Latitude")
    ap.add_argument("--lng", type=float, help="Longitude")
    ap.add_argument("--limit", type=int, default=3, help="How many results")
    ap.add_argument("--json", action="store_true", help="Output raw JSON if available")
    args = ap.parse_args()

    prompt = build_prompt(args)
    reply = await run_agent(prompt)

    if args.json:
        # best-effort: extract JSON blocks if present; otherwise just print reply
        try:
            start = reply.index("{")
            end   = reply.rindex("}") + 1
            print(json.dumps(json.loads(reply[start:end]), indent=2, ensure_ascii=False))
        except Exception:
            print(reply)
    else:
        print(reply)

if __name__ == "__main__":
    asyncio.run(main())
