import httpx
from src.config import SERPAPI_API_KEY

queries = [
    "site:myntra.com 33517605 reviews",
    "site:myntra.com aurelia embroidered kurta reviews",
    "site:meesho.com 2kp2tz reviews",
    "site:ajio.com 466686023 reviews",
]
with httpx.Client(timeout=40) as client:
    for query in queries:
        data = client.get(
            "https://serpapi.com/search.json",
            params={"engine": "google", "q": query, "gl": "in", "hl": "en", "api_key": SERPAPI_API_KEY},
        ).json()
        print("====", query, "n", len(data.get("organic_results") or []), data.get("error"))
        for row in (data.get("organic_results") or [])[:3]:
            print(" T", (row.get("title") or "")[:80])
            print(" L", (row.get("link") or "")[:160])
            print(" S", (row.get("snippet") or "")[:160].replace("\u20b9", "INR"))
