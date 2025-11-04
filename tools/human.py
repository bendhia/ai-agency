import asyncio, sys
from typing import Optional, List

_TTY_PATH = "/dev/tty"

async def _readline_from_tty() -> str:
    def _read():
        try:
            with open(_TTY_PATH, "r", encoding="utf-8", errors="ignore") as tty:
                return tty.readline()
        except Exception:
            return sys.stdin.readline()
    try:
        line = await asyncio.to_thread(_read)
        return (line or "").rstrip("\n").strip()
    except (EOFError, KeyboardInterrupt):
        return ""

async def ask_human(question: str, options: Optional[List[str]] = None, required: bool = True, max_retries: int = 1) -> str:
    tries = 0
    while True:
        print("\n=== CLARIFICATION NEEDED ===")
        print(question)
        if options:
            for i, opt in enumerate(options, 1):
                print(f"  {i}. {opt}")
        print("(Type your answer and press Enter)\n> ", end="", flush=True)

        answer = await _readline_from_tty()

        if options:
            try:
                n = int(answer)
                if 1 <= n <= len(options):
                    answer = options[n - 1]
            except (TypeError, ValueError):
                pass

        if answer:
            return answer
        if not required:
            return ""
        tries += 1
        if tries > max_retries:
            print("(No input received. Proceeding without an answer.)")
            return ""
        print("(No input received—please type an answer.)")
