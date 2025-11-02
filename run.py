import asyncio, sys
from agent import run_agent
if __name__ == "__main__":
  prompt = " ".join(sys.argv[1:]) or "Plan a 1-day walk in central Algiers with 3 sights and coffee stops."
  print(asyncio.run(run_agent(prompt)))


