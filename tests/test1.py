import requests
import time

data = {
    "currencies": ["USD", "GBP", "EUR", "JPY", "MXN", "AUD", "CHF"]
}

url = "http://127.0.0.1:8000/start_listening"  # Replace with your endpoint URL

response = requests.post(url, json=data)

print("Status code:", response.status_code)
print("Response JSON:", response.json())

session_id = response.json()['session_id']

time.sleep(1200)


url = f"http://127.0.0.1:8000/stop_listening/{session_id}"
requests.get(url)

url = f"http://127.0.0.1:8000/stats/{session_id}"
response = requests.get(url)

print("Stats Response JSON:", response.json())

url = f"http://127.0.0.1:8000/clear/{session_id}"
response = requests.get(url)