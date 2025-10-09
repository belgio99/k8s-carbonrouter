import base64, random, logging, os
from typing import Dict

loglevel = os.getenv("LOGLEVEL", "INFO").upper()
logging.basicConfig(level=loglevel,
                    format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger("carbonrouter")

def b64enc(data: bytes) -> str:
    return base64.b64encode(data).decode()

def b64dec(data: str | bytes) -> bytes:
    if isinstance(data, bytes):
        return base64.b64decode(data)
    return base64.b64decode(data.encode())

def weighted_choice(weights: Dict[str, int]) -> str:
    ks, vs = zip(*weights.items())
    return random.choices(ks, weights=vs, k=1)[0]

# Fallback schedule
DEFAULT_SCHEDULE = {
    "deadlines": {"high-power": 40, "mid-power": 120, "low-power": 300},
    "flavourWeights": {"high-power": 60, "mid-power": 30, "low-power": 10},
    "flavourRules": [
        {"flavourName": "high-power", "weight": 60, "deadlineSec": 40},
        {"flavourName": "mid-power", "weight": 30, "deadlineSec": 120},
        {"flavourName": "low-power", "weight": 10, "deadlineSec": 300},
    ],
    "processing": {
        "throttle": 1.0,
        "creditsRatio": 1.0,
        "intensityRatio": 1.0,
        "ceilings": {},
    },
    "validUntil": "2099-12-31T23:59:59Z",
}

# Debug mode flag and function
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

def debug(msg: str) -> None:
    """Print debug message if DEBUG env var is true."""
    if DEBUG:
        print(f"[DEBUG] {msg}")