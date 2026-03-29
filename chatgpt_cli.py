#!/usr/bin/env python3
"""FPREN CLI chat — routes through UF LiteLLM proxy."""

import os
from openai import OpenAI

BASE_URL = os.getenv("UF_LITELLM_BASE_URL", "https://api.ai.it.ufl.edu")
API_KEY  = os.getenv("UF_LITELLM_API_KEY", "")
MODEL    = os.getenv("UF_LITELLM_MODEL", "gpt-4o-mini")

if not API_KEY:
    raise SystemExit("UF_LITELLM_API_KEY is not set. Add it to your .env or environment.")

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

print(f"FPREN Chat CLI — model: {MODEL}  endpoint: {BASE_URL}")
print("Type 'exit' to quit.\n")

while True:
    user_input = input("You: ")
    if user_input.lower() == "exit":
        break

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": user_input}]
    )

    print("\nAssistant:", response.choices[0].message.content, "\n")
