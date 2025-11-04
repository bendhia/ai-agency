# agent.py
import os, json, asyncio, re
from dotenv import load_dotenv
from openai import OpenAI

# Tools
from tools.geo import (
    nominatim_search,
    osrm_walking_time,          # kept for compatibility
    nearby_search_with_walk,    # composite nearby (supports mode)
)
from tools.wiki import wikipedia_summary
from tools.human import ask_human
from tools.travel import plan_trip

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

# Coord auto-parser (helps the model include origin_lat/lng)
COORD_RE = re.compile(r"(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")
def _extract_coords(text: str):
    m = COORD_RE.search(text or "")
    if not m:
        return None
    lat, lng = float(m.group(1)), float(m.group(2))
    return lat, lng

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
    # Composite nearby tool
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
    # Trip planner tool
    {
        "type": "function",
        "function": {
            "name": "plan_trip",
            "description": "Create a day-by-day itinerary for a destination with optional dates, interests, and mode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {"type": "string"},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD (optional)"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD (optional)"},
                    "interests": {"type": "array", "items": {"type": "string"}},
                    "pace": {"type": "string", "enum": ["relaxed","moderate","intense"], "default": "moderate"},
                    "mode": {"type": "string", "enum": ["foot","bike","driving"], "default": "foot"},
                    "origin_lat": {"type": "number"},
                    "origin_lng": {"type": "number"},
                    "limit_per_day": {"type": "integer", "default": 4}
                },
                "required": ["destination"]
            }
        }
    },
]

SYSTEM = (
    "You are a travel/concierge agent. When the user asks for nearby places or trip planning, "
    "you MUST call tools and NEVER guess. "
    "If a tool returns distance_km / duration_min, display those verbatim. "
    "If the request is ambiguous (missing destination, dates, interests, mode, radius, origin coords), "
    "call ask_human to clarify and wait for the answer. "
    "If the user provides destination, dates and interests, you MUST call plan_trip. "
    "If the user message contains coordinates, ensure plan_trip includes origin_lat/origin_lng. "
    "Do NOT output empty assistant messages. If uncertain, call ask_human again."
)


def _looks_like_a_question(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    return ("?" in t) or t.startswith(("what ", "which ", "where ", "when ", "how ", "do you ", "are you "))

# ---------- agent ----------
async def run_agent(user_msg: str) -> str:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_msg}]

    # auto-nudge: if message contains coords, cache them and tell the model it can reuse
    orig = _extract_coords(user_msg)
    if orig:
        messages.append({
            "role": "system",
            "content": f"If you call plan_trip and origin_lat/lng are missing, use origin_lat={orig[0]} and origin_lng={orig[1]} extracted from the user message."
        })

    # Limit steps to prevent infinite loops
    for _ in range(12):
        try:
            first = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=TEMPERATURE,
            )
        except Exception as e:
            return f"Network error contacting OpenAI ({type(e).__name__}): {e}"

        assistant_msg = first.choices[0].message
        if DEBUG:
            print("DEBUG tool_calls:", getattr(assistant_msg, "tool_calls", None))
            print("DEBUG assistant content:", assistant_msg.content)

        if getattr(assistant_msg, "tool_calls", None):
            messages.append(assistant_msg)
            tool_messages = []

            for tc in assistant_msg.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments)
                if DEBUG: print("DEBUG calling tool:", name, args)

                # --- inject origin if missing for plan_trip ---
                if name == "plan_trip" and orig:
                    if args.get("origin_lat") is None or args.get("origin_lng") is None:
                        args["origin_lat"], args["origin_lng"] = orig

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
                elif name == "plan_trip":
                    out = await plan_trip(
                        args["destination"],
                        args.get("start_date"),
                        args.get("end_date"),
                        args.get("interests"),
                        args.get("pace","moderate"),
                        args.get("mode","foot"),
                        args.get("origin_lat"),
                        args.get("origin_lng"),
                        args.get("limit_per_day", 4),
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

            # Nearby-only short-circuit
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

            # Trip-only short-circuit
            if len(assistant_msg.tool_calls) == 1 and assistant_msg.tool_calls[0].function.name == "plan_trip":
                payload = json.loads(tool_messages[0]["content"])
                dest = payload.get("destination") or "Your trip"
                sd = payload.get("start_date") or "unspecified"
                ed = payload.get("end_date") or "unspecified"
                mode = payload.get("mode", "foot")

                itinerary = payload.get("itinerary") or []
                lines = [f"**Trip plan: {dest}** ({sd} → {ed}) — mode: {mode}\n"]

                if not itinerary:
                    lines.append("No itinerary items yet. Try adding dates, interests, or a starting location.")
                else:
                    for day in itinerary:
                        date_label = day.get("date") or "Day"
                        lines.append(f"- {date_label}:")
                        for it in day.get("items", []):
                            name = it.get("name") or "Place"
                            dist = f", ~{it.get('distance_km')} km" if it.get("distance_km") is not None else ""
                            dur = f", {it.get('duration_min')} min" if it.get("duration_min") is not None else ""
                            map_url = it.get("map_url") or "#"
                            lines.append(f"  • **{name}**{dist}{dur} — [Map]({map_url})")

                links = payload.get("links") or {}
                if links:
                    flights = links.get("flights")
                    hotels  = links.get("hotels")
                    if flights or hotels:
                        lines.append("")
                        if flights: lines.append(f"**Flights:** {flights}")
                        if hotels:  lines.append(f"**Hotels:** {hotels}")

                lines.append("\nTell me to refine by interests, pace, dates, or mode.")
                return "\n".join(lines)

            continue  # loop again to let the model compose final text if needed

        # No tool calls
        content = (assistant_msg.content or "").strip()
        if not content:
            messages.append({"role": "assistant", "content": ""})
            continue

        if _looks_like_a_question(content):
            answer = await ask_human(content, None, True)
            messages.extend([
                {"role": "assistant", "content": content},
                {"role": "user", "content": answer},
            ])
            continue

        return content

    return "Sorry, I couldn't complete the request in time. Please try again with a bit more detail."

if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]).strip() or "Plan me a quick outing near 36.7529, 3.0420."
    print(asyncio.run(run_agent(q)))
