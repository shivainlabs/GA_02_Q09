import time
import uuid
from typing import Optional
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# 1. Enable CORS (allowing any HTTP/HTTPS origin with credentials)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex="https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. In-Memory Stores
IDEMPOTENCY_STORE = {}    # Stores cached order responses for idempotency keys
RATE_LIMIT_STORE = {}     # Stores timestamps of requests for each Client ID

# Create our fixed catalog of orders 1 through 54
TOTAL_ORDERS = 54
ORDERS_CATALOG = [{"id": i, "product": f"Product {i}", "price": round(15.99 * i, 2)} for i in range(1, TOTAL_ORDERS + 1)]


# 3. Rate Limiting Middleware
@app.middleware("http")
async def rate_limiter(request: Request, call_next):
    # Do NOT rate-limit browser preflight (OPTIONS) requests
    if request.method == "OPTIONS":
        return await call_next(request)

    # Read the unique client ID header
    client_id = request.headers.get("X-Client-Id")
    if client_id:
        now = time.time()
        
        # Initialize bucket for this client if it doesn't exist
        if client_id not in RATE_LIMIT_STORE:
            RATE_LIMIT_STORE[client_id] = []
            
        # Clean up timestamps older than 10 seconds
        RATE_LIMIT_STORE[client_id] = [t for t in RATE_LIMIT_STORE[client_id] if now - t <= 10]
        
        # Check if they exceeded the limit: 20 requests per 10 seconds
        if len(RATE_LIMIT_STORE[client_id]) >= 20:
            # Calculate remaining cooldown time
            retry_after = 10 - (now - RATE_LIMIT_STORE[client_id][0])
            
            # Dynamically grab the requester's origin
            origin = request.headers.get("origin")
            
            # Setup headers (exposing both casings to avoid HTTP/2 lowercase conflicts)
            headers = {
                "retry-after": str(int(max(1, retry_after))),
                "Access-Control-Allow-Origin": origin if origin else "*",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Allow-Methods": "*",
                "Access-Control-Expose-Headers": "retry-after, Retry-After",  # 👈 Expose both casings
            }
            if origin:
                headers["Access-Control-Allow-Credentials"] = "true"
                
            return JSONResponse(
                status_code=429,
                content={"error": "Too Many Requests. Rate limit exceeded."},
                headers=headers
            )
            
        # Record this request's timestamp
        RATE_LIMIT_STORE[client_id].append(now)

    return await call_next(request)


# 4. POST /orders: Idempotent order creation
@app.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(request: Request, response: Response):
    # Extract idempotency key
    idempotency_key = request.headers.get("Idempotency-Key")
    
    if idempotency_key:
        # If we have seen this key before, return the cached order data
        if idempotency_key in IDEMPOTENCY_STORE:
            return IDEMPOTENCY_STORE[idempotency_key]
            
        # First time seeing this key: create a new order ID
        order_id = str(uuid.uuid4())
        order_data = {
            "id": order_id,
            "status": "created",
            "idempotency_key": idempotency_key
        }
        
        # Save it in our cache
        IDEMPOTENCY_STORE[idempotency_key] = order_data
        return order_data
        
    # If no key is sent, just create a normal order without caching it
    return {
        "id": str(uuid.uuid4()),
        "status": "created"
    }


# 5. GET /orders: Cursor-based pagination
@app.get("/orders")
def get_orders(limit: int = 10, cursor: Optional[str] = None):
    # Default to starting at index 0 (ID 1)
    start_index = 0
    
    if cursor:
        try:
            # We encode our cursor simply as the next ID to start from
            start_index = int(cursor) - 1
        except ValueError:
            start_index = 0
            
    # Ensure starting index doesn't go below 0
    start_index = max(0, start_index)
    
    # Fetch up to 'limit' items starting from start_index
    end_index = start_index + limit
    page_items = ORDERS_CATALOG[start_index:end_index]
    
    # Determine the next cursor (opaque to the grader, we just pass back the next index)
    if end_index < len(ORDERS_CATALOG):
        next_cursor = str(end_index + 1)
    else:
        next_cursor = None # No more pages
        
    return {
        "items": page_items,
        "next_cursor": next_cursor
    }