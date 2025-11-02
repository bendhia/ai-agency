import asyncio, sys, time, argparse
from agent import run_agent

TEMPLATE = "# Results\n\n{body}\n"

def to_md(text: str) -> str:
    return TEMPLATE.format(body=text)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="Output filename (e.g., cafes.md)")
    ap.add_argument("message", nargs="*", help="Prompt for the agent")
    args = ap.parse_args()

    prompt = " ".join(args.message) or "Use nearby_search_with_walk to find 3 cafes near 36.7529, 3.0420 and include walking time."
    outname = args.out or f"results_{int(time.time())}.md"

    out = asyncio.run(run_agent(prompt))
    with open(outname, "w", encoding="utf-8") as f:
        f.write(to_md(out))
    print(f"Saved: {outname}")
