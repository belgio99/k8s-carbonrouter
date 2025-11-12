#!/usr/bin/env python3
"""
Mock Carbon Intensity API Server for Testing

This server mimics the Carbon Intensity UK API but allows you to define
custom carbon intensity scenarios for controlled testing of the carbon-aware
scheduler.

Usage:
    python mock-carbon-api.py --scenario rising
    python mock-carbon-api.py --scenario peak --port 5001
    python mock-carbon-api.py --scenario custom --file custom-scenario.json

Scenarios:
    - rising: Morning pattern with increasing intensity
    - peak: Midday peak with sustained high intensity
    - falling: Evening pattern with decreasing intensity
    - low: Night pattern with very low intensity
    - volatile: Highly variable pattern for stress testing
    - custom: Load from JSON file
"""

import argparse
import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

import logging
from flask import Flask, jsonify, request

STEP_MINUTES = 0.25  # 15 seconds - matches carbon_scenario.json design

app = Flask(__name__)

# Configure a logger for the module; actual level is set in main() based on --debug
logger = logging.getLogger("mock-carbon-api")

# Predefined test scenarios (gCO2/kWh)
SCENARIOS = {
    "rising": {
        "name": "Morning Rising Pattern",
        "description": "Simulates morning increase in carbon intensity",
        "pattern": [120, 150, 180, 220, 280, 320, 350],
        "repeat": True,
    },
    "peak": {
        "name": "Midday Peak",
        "description": "Sustained high intensity period",
        "pattern": [300, 320, 330, 325, 320, 310, 305],
        "repeat": True,
    },
    "falling": {
        "name": "Evening Falling Pattern",
        "description": "Evening decrease in carbon intensity",
        "pattern": [280, 250, 200, 150, 100, 80, 60],
        "repeat": True,
    },
    "low": {
        "name": "Night Low Intensity",
        "description": "Very clean electricity overnight",
        "pattern": [50, 45, 40, 38, 35, 33, 30],
        "repeat": True,
    },
    "volatile": {
        "name": "Volatile Pattern",
        "description": "Highly variable for stress testing",
        "pattern": [100, 300, 80, 350, 120, 280, 90, 320, 110],
        "repeat": True,
    },
    "stable": {
        "name": "Stable Pattern",
        "description": "Relatively constant intensity",
        "pattern": [180, 185, 175, 190, 182, 188, 178],
        "repeat": True,
    },
    "extreme-peak": {
        "name": "Extreme Peak Event",
        "description": "Very high spike for emergency testing",
        "pattern": [150, 200, 350, 450, 500, 480, 420, 350, 280, 200],
        "repeat": False,
    },
    "extreme-clean": {
        "name": "Extremely Clean Period",
        "description": "Very low carbon for optimal conditions testing",
        "pattern": [100, 80, 50, 30, 20, 15, 25, 40, 60],
        "repeat": False,
    },
    "fast-test": {
        "name": "Fast Test Pattern (20 min)",
        "description": "Quick scenario for testing: good→bad→good with sudden spike at end",
        "pattern": [
            # Minutes 0-3: Good start (low carbon)
            50, 55, 60,
            # Minutes 3-10: Gradual degradation (getting worse)
            80, 120, 160, 200, 250, 300, 350,
            # Minutes 10-15: Moderate improvement
            300, 270, 240, 200, 180,
            # Minutes 15-18: Recovery (getting better)
            150, 120, 90,
            # Minutes 18-20: Sudden spike event (test reactiveness)
            400, 450
        ],
        "repeat": False,
    },
}

# Current active scenario
active_scenario = "fast-test"
custom_pattern = None

# Override start time for repeatable tests (None = use current time)
scenario_start_time = None


def generate_forecast_data(start_time: datetime, num_periods: int = 96) -> List[Dict[str, Any]]:
    """
    Generate mock forecast data in the format expected by the Carbon Intensity API.
    
    For each time period, returns:
    - from/to: Time window boundaries
    - intensity.forecast: Predicted intensity for this period (always present)
    - intensity.actual: Measured actual intensity (only for periods up to now, null for future)
    - intensity.index: Categorical intensity level
    
    The semantic difference:
    - For past/present periods: actual = what was measured, forecast = what was predicted
    - For future periods: forecast = what we predict, actual = null (unknown future)
    
    Args:
        start_time: Start of forecast window (typically "now")
        num_periods: Number of periods to generate (default 96 = 48 hours at 30-min intervals)
    """
    global active_scenario, custom_pattern, scenario_start_time
    
    data: List[Dict[str, Any]] = []
    
    # Determine which pattern to use
    if custom_pattern is not None:
        pattern = custom_pattern["pattern"]
        repeat = custom_pattern.get("repeat", True)
    else:
        scenario = SCENARIOS.get(active_scenario, SCENARIOS["rising"])
        pattern = scenario["pattern"]
        repeat = scenario.get("repeat", True)
    
    # Get current time to determine which periods are past vs future
    now = datetime.now(timezone.utc)
    
    # Calculate which index in the pattern we should start from
    # The pattern offset is based on time elapsed from scenario_start_time
    pattern_length = len(pattern)
    step_minutes = STEP_MINUTES
    
    if scenario_start_time is None:
        # Auto-initialize on first request: set scenario start to current time
        # This anchors the pattern to a specific point
        scenario_start_time = now.replace(second=0, microsecond=0)
    
    # Calculate elapsed time from scenario start to the requested start_time
    elapsed_minutes = (start_time - scenario_start_time).total_seconds() / 60
    # Calculate which step in the pattern we're at
    pattern_offset = int(elapsed_minutes / step_minutes)
    
    # Generate forecast entries
    for i in range(num_periods):
        # Calculate index into pattern, accounting for elapsed time
        pattern_index = pattern_offset + i
        
        # Get intensity value from pattern (repeat if configured)
        if repeat:
            intensity = pattern[pattern_index % pattern_length]
        else:
            intensity = pattern[pattern_index] if pattern_index < pattern_length else pattern[-1]
        
        # Add some small random variation for realism (±5%)
        # import random
        # variation = random.uniform(0.95, 1.05)
        # intensity = int(intensity * variation)
        
        # Calculate time window
        start = start_time + timedelta(minutes=step_minutes * i)
        end = start + timedelta(minutes=step_minutes)
        
        # Determine intensity index
        if intensity < 100:
            index = "very low"
        elif intensity < 150:
            index = "low"
        elif intensity < 200:
            index = "moderate"
        elif intensity < 300:
            index = "high"
        else:
            index = "very high"
        
        # For semantic consistency with real APIs:
        # - Include "actual" only for periods that have ended (end time <= now)
        # - Use "forecast" for all periods
        intensity_obj: Dict[str, Any] = {
            "forecast": intensity,
            "index": index
        }
        
        # Only include actual if this period has ended
        if end <= now:
            intensity_obj["actual"] = intensity
        
        data.append({
            "from": start.strftime("%Y-%m-%dT%H:%MZ"),
            "to": end.strftime("%Y-%m-%dT%H:%MZ"),
            "intensity": intensity_obj
        })
    
    return data


@app.route('/intensity/<start_time>/fw48h')
@app.route('/intensity/<start_time>/fw48h/regionid/<region_id>')
@app.route('/intensity/<start_time>/fw48h/postcode/<postcode>')
def get_forecast(start_time: str, region_id: str = None, postcode: str = None):
    """
    Return mock forecast schedule in Carbon Intensity API format.
    
    Supports national, regional, and postcode endpoints.
    """
    try:
        # Parse start time - support both minute and second precision
        if start_time.endswith('Z'):
            # Try with seconds first, fall back to minutes only
            try:
                start = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                start = datetime.strptime(start_time, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
        else:
            start = datetime.fromisoformat(start_time).astimezone(timezone.utc)
    except ValueError:
        return jsonify({"error": "Invalid timestamp format"}), 400
    
    # Generate forecast data
    data = generate_forecast_data(start)
    
    # Add regional context if requested
    response = {"data": data}
    if region_id:
        response["region"] = {"regionid": int(region_id), "shortname": f"Region {region_id}"}
    elif postcode:
        response["postcode"] = postcode.upper()
    
    return jsonify(response)


# Request/response logging for debugging
@app.before_request
def log_request_info():
    try:
        body = None
        if request.method in ("POST", "PUT", "PATCH"):
            # attempt to show JSON body if present
            body = request.get_json(silent=True)
        logger.debug("Incoming request: %s %s | args=%s | json=%s", request.method, request.path, dict(request.args), body)
    except Exception:
        # Never let logging break the request handling
        logger.exception("Error while logging request")


@app.after_request
def log_response_info(response):
    try:
        # Try to log JSON/text response body (truncate to avoid huge logs)
        data = response.get_data(as_text=True)
        max_len = 2000
        if data and len(data) > max_len:
            data_preview = data[:max_len] + "...[truncated]"
        else:
            data_preview = data
        logger.debug("Response: %s %s | status=%s | body=%s", request.method, request.path, response.status, data_preview)
    except Exception:
        logger.exception("Error while logging response")
    return response


@app.route('/intensity')
def get_current():
    """Return current intensity (first point in forecast)."""
    # Get current time floored to step-minute boundary
    now = datetime.now(timezone.utc)
    step_minutes = max(1, int(STEP_MINUTES))
    minute = (now.minute // step_minutes) * step_minutes
    start = now.replace(minute=minute, second=0, microsecond=0)
    
    data = generate_forecast_data(start, num_periods=1)
    if data:
        return jsonify({"data": [data[0]]})
    return jsonify({"data": []})


@app.route('/scenario', methods=['GET'])
def get_scenario():
    """Get current active scenario."""
    if custom_pattern:
        return jsonify({
            "scenario": "custom",
            "pattern": custom_pattern["pattern"],
            "repeat": custom_pattern["repeat"]
        })
    
    scenario = SCENARIOS.get(active_scenario, SCENARIOS["rising"])
    return jsonify({
        "scenario": active_scenario,
        "name": scenario["name"],
        "description": scenario["description"],
        "pattern": scenario["pattern"],
        "available_scenarios": list(SCENARIOS.keys())
    })


@app.route('/scenario', methods=['POST'])
def set_scenario():
    """Change active scenario at runtime."""
    global active_scenario, custom_pattern
    
    data = request.get_json() or {}
    new_scenario = data.get("scenario")
    
    if new_scenario == "custom":
        pattern = data.get("pattern")
        if not pattern or not isinstance(pattern, list):
            return jsonify({"error": "Custom scenario requires 'pattern' array"}), 400
        custom_pattern = {
            "pattern": pattern,
            "repeat": data.get("repeat", True)  # Default to repeating
        }
        return jsonify({
            "status": "scenario updated",
            "scenario": "custom",
            "pattern": custom_pattern["pattern"],
            "repeat": custom_pattern["repeat"]
        })
    
    if new_scenario not in SCENARIOS:
        return jsonify({
            "error": f"Unknown scenario: {new_scenario}",
            "available": list(SCENARIOS.keys())
        }), 400
    
    active_scenario = new_scenario
    custom_pattern = None
    
    scenario = SCENARIOS[active_scenario]
    return jsonify({
        "status": "scenario updated",
        "scenario": active_scenario,
        "name": scenario["name"],
        "description": scenario["description"]
    })


@app.route('/reset', methods=['POST'])
def reset_scenario():
    """Reset scenario to start from the beginning of the pattern."""
    global scenario_start_time
    
    # Set start time to beginning of current minute
    scenario_start_time = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    
    return jsonify({
        "status": "scenario reset",
        "start_time": scenario_start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "message": "Pattern will restart from beginning"
    })


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "mock-carbon-api"})


@app.route('/')
def index():
    """API documentation."""
    return jsonify({
        "service": "Mock Carbon Intensity API",
        "version": "1.0",
        "endpoints": {
            "/intensity/<timestamp>/fw48h": "Get 48-hour forecast from timestamp",
            "/intensity": "Get current intensity",
            "/scenario [GET]": "Get active scenario",
            "/scenario [POST]": "Change scenario (body: {\"scenario\": \"name\"})",
            "/reset [POST]": "Reset scenario to start from beginning",
            "/health": "Health check"
        },
        "current_scenario": active_scenario if not custom_pattern else "custom",
        "available_scenarios": list(SCENARIOS.keys())
    })


def load_custom_scenario(filepath: str) -> dict:
    """Load custom scenario from JSON file."""
    with open(filepath, 'r') as f:
        data = json.load(f)
    
    if isinstance(data, list):
        # Convert simple array to full scenario format
        return {
            "pattern": data,
            "repeat": True  # Default to repeating for custom scenarios
        }
    elif isinstance(data, dict) and "pattern" in data:
        # Full scenario format - ensure repeat defaults to True
        if "repeat" not in data:
            data["repeat"] = True
        return data
    else:
        raise ValueError("JSON must be array of numbers or object with 'pattern' key")


def main():
    parser = argparse.ArgumentParser(
        description="Mock Carbon Intensity API Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start with rising scenario
  python mock-carbon-api.py --scenario rising
  
  # Use custom pattern from file
  python mock-carbon-api.py --scenario custom --file my-pattern.json
  
  # Run on different port
  python mock-carbon-api.py --port 5001
  
  # Change scenario at runtime
  curl -X POST http://localhost:5000/scenario -H "Content-Type: application/json" \\
    -d '{"scenario": "peak"}'
        """
    )
    
    parser.add_argument(
        '--scenario',
        choices=list(SCENARIOS.keys()) + ['custom'],
        default='rising',
        help='Initial scenario to use'
    )
    
    parser.add_argument(
        '--file',
        type=str,
        help='JSON file with custom pattern (for --scenario custom)'
    )
    
    parser.add_argument(
        '--port',
        type=int,
        default=5000,
        help='Port to run server on (default: 5000)'
    )
    
    parser.add_argument(
        '--host',
        type=str,
        default='0.0.0.0',
        help='Host to bind to (default: 0.0.0.0)'
    )

    parser.add_argument(
        '--step-minutes',
        type=float,
        default=0.25,
        help='Duration of each forecast slot in minutes (default: 0.25 = 15 seconds)'
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    
    args = parser.parse_args()
    
    global active_scenario, custom_pattern, STEP_MINUTES

    # Configure logging now that args are known
    logging.basicConfig(format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    level = logging.DEBUG if args.debug else logging.INFO
    logger.setLevel(level)
    # Also configure Flask's own logger
    app.logger.setLevel(level)
    
    if args.scenario == 'custom':
        if not args.file:
            parser.error("--file required when using --scenario custom")
        custom_pattern = load_custom_scenario(args.file)
        active_scenario = "custom"
        print(f"Loaded custom scenario from {args.file}")
        print(f"Pattern: {custom_pattern['pattern']}")
        print(f"Repeat: {custom_pattern['repeat']}")
    else:
        active_scenario = args.scenario
        scenario = SCENARIOS[active_scenario]
        print(f"Starting with scenario: {scenario['name']}")
        print(f"Description: {scenario['description']}")
        print(f"Pattern: {scenario['pattern']}")
    
    STEP_MINUTES = args.step_minutes
    print(f"Time step: {STEP_MINUTES} minute(s)")

    print(f"\nServer starting on http://{args.host}:{args.port}")
    print("\nEndpoints:")
    print(f"  - GET  http://{args.host}:{args.port}/intensity/<timestamp>/fw48h")
    print(f"  - GET  http://{args.host}:{args.port}/intensity")
    print(f"  - GET  http://{args.host}:{args.port}/scenario")
    print(f"  - POST http://{args.host}:{args.port}/scenario")
    print("\nChange scenario at runtime:")
    print(f"  curl -X POST http://{args.host}:{args.port}/scenario -H 'Content-Type: application/json' -d '{{\"scenario\": \"peak\"}}'")
    print()
    
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()
