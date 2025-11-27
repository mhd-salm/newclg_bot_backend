import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
print("Using API KEY:", api_key[:10] + "**************")

genai.configure(api_key=api_key)

print("\nFetching available models...\n")

models = genai.list_models()

for m in models:
    print("------------------------------------------------")
    print("Model name:", m.name)
    if hasattr(m, "supported_generation_methods"):
        print("Supported methods:", m.supported_generation_methods)
    print("------------------------------------------------\n")
