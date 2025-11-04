# chat.py
import asyncio
from agent import run_agent

async def repl():
    print("Travel Agent chat — type 'exit' to quit.")
    while True:
        try:
            msg = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            return
        if msg.lower() in {"exit", "quit"}:
            print("Bye!")
            return
        reply = await run_agent(msg)
        print("\nAgent:\n" + (reply or ""))

if __name__ == "__main__":
    asyncio.run(repl())

