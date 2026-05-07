#!/usr/bin/env python
"""v0.3.1 smoke test: verify reasoning_text and reasoning_tokens populate.

Sends a single tiny prompt to deepseek-v4-flash with the new in-package
extra_body thinking activation, then prints the DispatchResult to confirm
both reasoning fields are populated.
"""
import asyncio
import os
from xaxiu_swarm.backends.deepseek import DeepSeekBackend


async def main():
    backend = DeepSeekBackend(model="deepseek-v4-flash")
    print(f"Backend default_model: {backend.default_model}")
    print(f"DEEPSEEK_API_KEY set: {bool(backend.api_key)}")
    print()
    result = await backend.dispatch_async(
        prompt="In 2 sentences explain why prefix caching matters for LLM API costs.",
        timeout=120,
    )
    print(f"status:           {result.status}")
    print(f"model:            {result.model}")
    print(f"elapsed_s:        {result.elapsed_s:.2f}")
    print(f"request_tokens:   {result.request_tokens}")
    print(f"response_tokens:  {result.response_tokens}")
    print(f"reasoning_tokens: {result.reasoning_tokens}")
    print(f"reasoning_text first 200 chars: {(result.reasoning_text or '')[:200]!r}")
    print(f"response first 200 chars:       {result.response[:200]!r}")
    print()
    print("PASS" if (result.status == "completed" and result.reasoning_text and result.reasoning_tokens) else "FAIL")


if __name__ == "__main__":
    asyncio.run(main())
