import os, asyncio, argparse
from dotenv import load_dotenv
from agent import run_agent

load_dotenv()

def build_prompt(args):
    if args.message:  # if user passed a free-text prompt, use it directly
        return " ".join(args.message).strip()

    q     = args.q or "cafes"
    lat   = args.lat or float(os.getenv("DEFAULT_LAT", "36.7529"))
    lng   = args.lng or float(os.getenv("DEFAULT_LNG", "3.0420"))
    limit = args.limit
    mode  = args.mode or os.getenv("DEFAULT_MODE", "foot")

    return f"Use nearby_search_with_walk to find {limit} {q} near {lat}, {lng} in {mode} mode and include time."

async def main():
    ap = argparse.ArgumentParser(description="Run the nearby agent via CLI")
    ap.add_argument("--q", help="What to search (e.g., cafes, pizza, museum)")
    ap.add_argument("--lat", type=float, help="Latitude")
    ap.add_argument("--lng", type=float, help="Longitude")
    ap.add_argument("--limit", type=int, default=int(os.getenv("DEFAULT_LIMIT", "3")), help="How many results")
    ap.add_argument("--mode", choices=["foot","bike","driving"], help="Travel mode (default from DEFAULT_MODE)")
    # everything after flags is treated as a free-text prompt
    ap.add_argument("message", nargs="*", help="Optional free-text prompt")
    args = ap.parse_args()

    prompt = build_prompt(args)
    print(await run_agent(prompt))

if __name__ == "__main__":
    asyncio.run(main())
