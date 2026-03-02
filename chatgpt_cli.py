#!/home/lh_admin/chatgpt_env/bin/python

from openai import OpenAI
client = OpenAI()

print("ChatGPT CLI - type 'exit' to quit")

while True:
    user_input = input("You: ")
    if user_input.lower() == "exit":
        break

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": user_input}]
    )

    print("\nChatGPT:", response.choices[0].message.content)
