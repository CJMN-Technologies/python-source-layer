import os
from dotenv import load_dotenv
from google import genai

load_dotenv(".env")
api_key = os.getenv("GEMINI_API_KEY", "").split(",")[0].strip()

try:
    client = genai.Client(api_key=api_key)
    models = client.models.list()
    print("AVAILABLE MODELS:")
    for m in models:
        if 'flash' in m.name.lower() or 'gemini' in m.name.lower():
            print(f"- {m.name}")
except Exception as e:
    print(f"Error: {e}")
