# agent.py
import os
import json
import asyncio
from dotenv import load_dotenv
from openai import OpenAI

# Tools
from tools.geo import (
    nominatim_search,
    osrm_walking_time,          # legacy single-route tool (still supported)
    nearby_search_with_walk,    # composite tool (supports mode)
)
from tools.wiki import wikipedia_summary
from tools.human import ask_human  # in-terminal clarification tool

# ---------- config ----------
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("MODEL", "gpt-4o-mini")
try:
    TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))
except ValueError:
    TEMPERATURE = 0.2
DEBUG = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")

client = OpenAI(api_key=OPENAI_API_KEY)

# ---------- tools schema for function calling ----------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "nominatim_search",
            "description": "Find places by text search (use for 'near me', POIs, cafes, etc.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "lat": {"type": "number"},
                    "lng": {"type": "number"},
                    "limit": {"type": "integer", "default": 3},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "osrm_walking_time",
            "description": "Walking time between two coords",
            "parameters": {
                "type": "object",
                "properties": {
                    "o_lat": {"type": "number"},
                    "o_lng": {"type": "number"},
                    "d_lat": {"type": "number"},
                    "d_lng": {"type": "number"},
                },
                "required": ["o_lat", "o_lng", "d_lat", "d_lng"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wikipedia_summary",
            "description": "Short summary for a POI",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                },
                "required": ["title"],
            },
        },
    },
    # Composite tool (supports mode)
    {
        "type": "function",
        "function": {
            "name": "nearby_search_with_walk",
            "description": "Search nearby places and include travel time from the origin. Preferred for nearby queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "origin_lat": {"type": "number"},
                    "origin_lng": {"type": "number"},
                    "limit": {"type": "integer", "default": 3},
                    "mode": {
                        "type": "string",
                        "enum": ["foot", "bike", "driving"],
                        "default": "foot",
                    },
                },
                "required": ["query", "origin_lat", "origin_lng"],
            },
        },
    },
    # Ask the human when unclear
    {
        "type": "function",
        "function": {
            "name": "ask_human",
            "description": "Ask the user a clarifying question when the goal is unclear. Use before guessing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "required": {"type": "boolean", "default": True},
                },
                "required": ["question"],
            },
        },
    },
]

SYSTEM = (
    "You are a travel/concierge agent. When the user asks for places near coordinates, cafes, landmarks, or "
    "distances, you MUST call tools and NEVER guess. "
    "If the tool returns fields like distance_km and duration_min, display those values verbatim. "
    "If the request is ambiguous or missing key constraints (e.g., category, radius, budget, time, mode), "
    "you MUST call ask_human to clarify and wait for the answer before continuing. "
    "Do NOT ask clarifying questions as plain assistant messages; ALWAYS use the ask_human tool for every clarification."
)

def _looks_like_a_question(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    # heuristic: a short line that ends with '?' or starts with wh-words
    return ("?" in t) or t.startswith(("what ", "which ", "where ", "when ", "how ", "do you ", "are you "))

# ---------- agent (interactive loop) ----------
async def run_agent(user_msg: str) -> str:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_msg}]

    # Limit steps to prevent infinite loops
    for _ in range(8):
        # 1) Ask the model what to do next
        first = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=TEMPERATURE,
        )
        assistant_msg = first.choices[0].message
        if DEBUG:
            print("DEBUG tool_calls:", getattr(assistant_msg, "tool_calls", None))
            print("DEBUG assistant content:", assistant_msg.content)

        # A) If the model requested tool(s), run them all and append results
        if getattr(assistant_msg, "tool_calls", None):
            messages.append(assistant_msg)

            tool_messages = []
            for tc in assistant_msg.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments)
                if DEBUG:
                    print("DEBUG calling tool:", name, args)

                if name == "nominatim_search":
                    out = await nominatim_search(
                        args["query"], args.get("lat"), args.get("lng"), args.get("limit", 3)
                    )
                elif name == "osrm_walking_time":
                    out = await osrm_walking_time(
                        args["o_lat"], args["o_lng"], args["d_lat"], args["d_lng"]
                    )
                elif name == "wikipedia_summary":
                    out = await wikipedia_summary(args["title"])
                elif name == "nearby_search_with_walk":
                    out = await nearby_search_with_walk(
                        args["query"],
                        args["origin_lat"],
                        args["origin_lng"],
                        args.get("limit", 3),
                        args.get("mode", "foot"),
                    )
                elif name == "ask_human":
                    out = await ask_human(
                        args["question"],
                        args.get("options"),
                        args.get("required", True),
                    )
                else:
                    out = {"error": f"unknown tool: {name}"}

                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": json.dumps(out),
                }
                tool_messages.append(tool_msg)
                messages.append(tool_msg)

            # 2.5) Short-circuit if only the composite tool was used (deterministic formatting)
            if len(assistant_msg.tool_calls) == 1 and assistant_msg.tool_calls[0].function.name == "nearby_search_with_walk":
                payload = json.loads(tool_messages[0]["content"])
                cards = payload.get("cards", [])
                if not cards:
                    return "No results found."
                origin = payload.get("origin", {})
                mode = payload.get("mode", "foot")
                lines = [
                    f"Here are {min(len(cards), 3)} places near ({origin.get('lat')}, {origin.get('lng')}) — mode: **{mode}**:\n"
                ]
                for i, c in enumerate(cards[:3], 1):
                    lines.append(
                        f"{i}. **{c['name']}**\n"
                        f"   - ~{c['distance_km']} km, {c['duration_min']} min on {mode}\n"
                        f"   - [Map]({c['map_url']})\n"
                    )
                return "\n".join(lines)

            # Otherwise continue the loop; the next iteration will let the model compose a final answer
            continue

        # B) No tool calls. If it looks like a question, FORCE a clarification via ask_human, then loop.
        content = (assistant_msg.content or "").strip()
        if _looks_like_a_question(content):
            # Ask the user in-terminal, append the answer, and continue the loop
            answer = await ask_human(content, None, True)
            messages.extend([
                {"role": "assistant", "content": content},
                {"role": "user", "content": answer},
            ])
            continue

        # C) Otherwise it's a normal final message
        return content

    # Fallback if loop exceeds steps
    return "Sorry, I couldn't complete the request in time. Please try again with a bit more detail."

if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]).strip() or (
        "Plan me a quick outing near 36.7529, 3.0420."
    )
    print(asyncio.run(run_agent(q)))
