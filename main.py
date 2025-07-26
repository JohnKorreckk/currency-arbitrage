from fastapi import FastAPI, HTTPException
import threading
import time
import redis
from twelvedata import TDClient
from contextlib import asynccontextmanager
import os
from pydantic import BaseModel
from typing import List
import uuid
import math

# Initialize API key and TwelveData client
api_key = os.getenv("TWELVEDATA_API_KEY")
td = TDClient(apikey=api_key)

# Initialize Redis
r = redis.Redis(host='localhost', port=6379, db=0)

# Global session management
listener_threads = {}  # session_id -> thread
detection_threads = {} # session_id -> thread
stop_events = {}       # session_id -> threading.Event

# Available symbols cache
available_symbols = set()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global available_symbols
    pairs = td.custom_endpoint(name="forex_pairs").as_json()
    available_symbols = {pair['symbol'] for pair in pairs}
    print(f"Cached {len(available_symbols)} available symbols.")
    yield
    print("Shutting down...")

app = FastAPI(lifespan=lifespan)

# Data model for arbitrage request
class ArbitrageRequest(BaseModel):
    currencies: List[str]

# CurrencyGraph class (simplified here)
class CurrencyGraph:
    def __init__(self, currencies):
        self.nodes = currencies
        self.edges = {currency: {} for currency in currencies}
    
    def add_edge(self, from_currency, to_currency, rate):
        self.edges[from_currency][to_currency] = rate
    
    def __str__(self):
        return str(self.edges)
    
    def PopulateWithPricesrices(self, td_client, pairs):
        for pair in pairs:
            data = td_client.price(symbol=pair).as_json()
            if 'price' in data:
                from_currency, to_currency = pair.split('/')
                self.add_edge(from_currency, to_currency, float(data['price']))
    
    def DetectNegativeCycle(self):
        # Convert edges to graph with weights = -log(rate)
        graph = self.edges
        nodes = self.nodes
        dist = {node: float('inf') for node in nodes}
        predecessor = {node: None for node in nodes}
        
        # Profit threshold
        threshold = -math.log(1.005)

        # Arbitrary start node (can run from any node)
        start = nodes[0]
        dist[start] = 0
        
        # Relax edges |V|-1 times
        for _ in range(len(nodes) - 1):
            updated = False
            for u in nodes:
                for v in graph[u]:
                    weight = -math.log(graph[u][v])
                    if dist[u] + weight < dist[v]:
                        dist[v] = dist[u] + weight
                        predecessor[v] = u
                        updated = True
            if not updated:
                break
        
        # Check for negative cycle
        for u in nodes:
            for v in graph[u]:
                weight = -math.log(graph[u][v])
                if dist[u] + weight < dist[v] + threshold:
                    # Negative cycle detected, reconstruct it
                    cycle = []
                    visited = set()
                    current = v
                    # To ensure cycle, move |V| times backwards
                    for _ in range(len(nodes)):
                        current = predecessor[current]
                    cycle_start = current
                    
                    # Build cycle path
                    while True:
                        if current in visited and current == cycle_start:
                            break
                        visited.add(current)
                        cycle.append(current)
                        current = predecessor[current]
                    cycle.append(cycle_start)
                    cycle.reverse()
                    return cycle
        return None

# Generate all bidirectional currency pairs
def generate_bidirectional_pairs(currencies: List[str]) -> List[str]:
    pairs = []
    for i in range(len(currencies)):
        for j in range(len(currencies)):
            if i != j:
                pairs.append(f"{currencies[i]}/{currencies[j]}")
    return pairs

# Split currency pairs
def split_pairs(str):
    word1 = ""
    word2 = ""
    count = 0
    while str[count] != '/' and count < len(str):
        word1 = word1 + str[count]
        count += 1
    count += 1
    while count < len(str):
        word2 = word2 + str[count]
        count += 1
    return word1, word2

# WebSocket listener function (per session)
def ws_listener_session(session_id, currencies, currencyGraph: CurrencyGraph):
    td = TDClient(apikey=api_key)

    def on_event(e):
        print(e)
        if e.get('event') == 'price':
            symbol = e.get('symbol')
            price = e.get('price')

            if symbol and price:
                from_currency, to_currency = split_pairs(symbol)
                currencyGraph.add_edge(from_currency, to_currency, price)
                # Store in Redis under session-specific key

    ws = td.websocket(symbols=currencies, on_event=on_event)
    ws.connect()
    while not stop_events[session_id].is_set():
        ws.heartbeat()
        stop_events[session_id].wait(10)
    ws.disconnect()

def scheduled_detection(session_id, currencyGraph: CurrencyGraph, stop_event):
    while not stop_event.is_set():
        cycle = currencyGraph.DetectNegativeCycle()
        if cycle:
            # Format and store arbitrage opportunity
            arbitrage_opportunity = f"{cycle}"
            r.lpush(f"opportunities:{session_id}", arbitrage_opportunity)
            print("Arbitrage detected and stored in Redis.")

        # Wait for 1 second before next detection
        stop_event.wait(1)

# API endpoint: start listening
@app.post("/start_listening")
def start_listening(data: ArbitrageRequest):
    currency_pairs = generate_bidirectional_pairs(data.currencies)
    currencyGraph = CurrencyGraph(data.currencies)

    # Validate pairs
    for pair in currency_pairs:
        if pair not in available_symbols:
            raise HTTPException(status_code=400, detail=f"Exchange pair: {pair} not available")


    # Initialize CurrencyGraph efficiently with batch request
    currencyGraph = CurrencyGraph(data.currencies)
    batch_symbols = ",".join(currency_pairs)
    batch_response = td.price(symbol=batch_symbols).as_json()

    for symbol, info in batch_response.items():
        price = info.get('price')
        if price:
            base, quote = symbol.split('/')
            currencyGraph.add_edge(base, quote, float(price))
        else:
            print(f"Warning: No price for {symbol}")

    # Create unique session ID
    session_id = str(uuid.uuid4())
    stop_event = threading.Event()
    stop_events[session_id] = stop_event

    # Start listener thread for this session
    thread = threading.Thread(
        target=ws_listener_session,
        args=(session_id, ",".join(currency_pairs), currencyGraph),
        daemon=True
    )
    listener_threads[session_id] = thread
    thread.start()

    # Start scheduled detection thread
    detection_thread = threading.Thread(
        target=scheduled_detection,
        args=(session_id, currencyGraph, stop_event),
        daemon=True
    )
    detection_threads[session_id] = detection_thread
    detection_thread.start()

    return {"status": "Started listening.", "session_id": session_id}

# API endpoint: stop listening
@app.get("/stop_listening/{session_id}")
def stop_listening(session_id: str):
    stop_event = stop_events.get(session_id)
    thread = listener_threads.get(session_id)
    if not stop_event or not thread:
        raise HTTPException(status_code=404, detail=f"No such session: {session_id}")

    stop_event.set()
    thread.join(timeout=5)

    # Clean up
    del stop_events[session_id]
    del listener_threads[session_id]

    return {"status": f"Stopped listening for session {session_id}."}

# API endpoint: get stats for a session
@app.get("/stats/{session_id}")
def stats(session_id: str):
    opportunities = r.lrange(f"opportunities:{session_id}", 0, -1)
    opportunities = [op.decode('utf-8') for op in opportunities]
    return {"session_id": session_id, "opportunities": opportunities}

# API endpoint: clear stats for a session
@app.get("/clear/{session_id}")
def clear_db(session_id: str):
    r.delete(f"opportunities:{session_id}")
    return {"status": f"Cleared Redis arbitrage_opportunities list for session {session_id}."}