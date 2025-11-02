import os, json, asyncio
from dotenv import load_dotenv
from openai import OpenAI
from tools.geo import nominatim_search, osrm_walking_time, nearby_search_with_walk
from tools.wiki import wikipedia_summary

DEBUG = False  # set True to print tool_calls etc.

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

TOOLS = [
  {"type":"function","function":{
    "name":"nominatim_search",
    "description":"Find places by text search (use for 'near me', POIs, cafes, etc.)",
    "parameters":{"type":"object","properties":{
      "query":{"type":"string"},
      "lat":{"type":"number"},
      "lng":{"type":"number"},
      "limit":{"type":"integer","default":3}
    },"required":["query"]}}
  },
  {"type":"function","function":{
    "name":"osrm_walking_time",
    "description":"Walking time between two coords",
    "parameters":{"type":"object","properties":{
      "o_lat":{"type":"number"},"o_lng":{"type":"number"},
      "d_lat":{"type":"number"},"d_lng":{"type":"number"}
    },"required":["o_lat","o_lng","d_lat","d_lng"]}}
  },
  {"type":"function","function":{
    "name":"wikipedia_summary",
    "description":"Short summary for a POI",
    "parameters":{"type":"object","properties":{
      "title":{"type":"string"}
    },"required":["title"]}}
  },
  # NEW composite tool
  {"type":"function","function":{
    "name":"nearby_search_with_walk",
    "description":"Search nearby places and include walking time from the origin. Preferred for nearby queries.",
    "parameters":{"type":"object","properties":{
      "query":{"type":"string"},
      "origin_lat":{"type":"number"},
      "origin_lng":{"type":"number"},
      "limit":{"type":"integer","default":3}
    },"required":["query","origin_lat","origin_lng"]}}
  }
]

SYSTEM = (
  "You are a travel/concierge agent. When the user asks for places near coordinates, cafes, landmarks, or distances, "
  "you MUST call the tools and NEVER guess. If the tool returns fields like distance_km and duration_min, "
  "you MUST display those values verbatim without recomputing or changing units."
)

async def run_agent(user_msg: str) -> str:
    # 1) First call lets the model decide which tools to call
    messages = [{"role":"system","content":SYSTEM},{"role":"user","content":user_msg}]
    first = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        temperature=0.3
    )
    assistant_msg = first.choices[0].message
    if DEBUG:
        print("DEBUG tool_calls:", getattr(assistant_msg, "tool_calls", None))

    # If no tool calls, just return content
    if not getattr(assistant_msg, "tool_calls", None):
        return assistant_msg.content

    # 2) Execute tool calls and collect tool messages
    tool_messages = []
    for tc in assistant_msg.tool_calls:
        name = tc.function.name
        args = json.loads(tc.function.arguments)

        if name == "nominatim_search":
            out = await nominatim_search(args["query"], args.get("lat"), args.get("lng"), args.get("limit",3))
        elif name == "osrm_walking_time":
            out = await osrm_walking_time(args["o_lat"], args["o_lng"], args["d_lat"], args["d_lng"])
        elif name == "wikipedia_summary":
            out = await wikipedia_summary(args["title"])
        elif name == "nearby_search_with_walk":
            out = await nearby_search_with_walk(
                args["query"], args["origin_lat"], args["origin_lng"], args.get("limit",3)
            )
        else:
            out = {"error": f"unknown tool: {name}"}

        tool_messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "name": name,
            "content": json.dumps(out)
        })

    # >>> THIS IS THE BLOCK YOU WERE ASKING ABOUT <<<
    # If the model called the composite tool, format a reliable answer ourselves
    only_one = len(assistant_msg.tool_calls) == 1
    if only_one and assistant_msg.tool_calls[0].function.name == "nearby_search_with_walk":
        payload = json.loads(tool_messages[0]["content"])  # {"cards":[...], "origin": {...}}
        cards = payload.get("cards", [])
        if not cards:
            return "No results found."
        lines = [f"Here are {min(len(cards),3)} cafes near ({payload['origin']['lat']}, {payload['origin']['lng']}):\n"]
        for i, c in enumerate(cards[:3], 1):
            lines.append(
                f"{i}. **{c['name']}**\n"
                f"   - ~{c['distance_km']} km, {c['duration_min']} min on foot\n"
                f"   - [Map]({c['map_url']})\n"
            )
        return "\n".join(lines)

    # 3) Second call with the assistant tool_calls + tool outputs
    follow = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages + [assistant_msg] + tool_messages,
        temperature=0.3
    )
    return follow.choices[0].message.content

if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Find 3 cafes near 36.7529, 3.0420 and show walking time."
    print(asyncio.run(run_agent(q)))
