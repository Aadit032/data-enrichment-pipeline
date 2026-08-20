import requests
import os

url = "https://api.groq.com/openai/v1/models"

headers = {
"Authorization": f"Bearer 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",
    "Content-Type": "application/json"
}

response = requests.get(url, headers=headers)

print(response.json())