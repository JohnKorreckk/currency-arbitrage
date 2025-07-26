```markdown
# Forex Arbitrage Detector

A FastAPI backend that detects currency arbitrage opportunities using live Forex data from TwelveData and stores them in Redis for quick retrieval.

## Requirements

- Python 3.9+
- Redis (running locally)
- TwelveData API key (`TWELVEDATA_API_KEY`)

## Setup

```bash
pip install fastapi uvicorn redis twelvedata pydantic
export TWELVEDATA_API_KEY=your_api_key
uvicorn main:app --reload
```

## Endpoints

- `POST /start_listening` — Start a session with a list of currencies  
  Body: `{ "currencies": ["USD", "EUR", "JPY"] }`

- `GET /stop_listening/{session_id}` — Stop a session

- `GET /stats/{session_id}` — Get detected arbitrage opportunities

- `GET /clear/{session_id}` — Clear opportunities from Redis
```