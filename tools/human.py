# tools/human.py
import asyncio
from typing import Optional, List

async def ask_human(question: str, options: Optional[List[str]] = None, required: bool = True) -> str:
    """
    Ask the user a clarifying question in the terminal and return their answer.
    If options are provided, user may answer by typing the option number or text.
    """
    print("\n=== CLARIFICATION NEEDED ===")
    print(question)
    if options:
        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt}")
    print("(Type your answer and press Enter)\n> ", end="", flush=True)

    loop = asyncio.get_running_loop()
    answer = await loop.run_in_executor(None, input)
    answer = (answer or "").strip()

    # If options exist and the user typed a number, map it to the option
    if options:
        try:
            num = int(answer)
            if 1 <= num <= len(options):
                answer = options[num - 1]
        except ValueError:
            pass

    if required and not answer:
        return "[no answer provided]"
    return answer

