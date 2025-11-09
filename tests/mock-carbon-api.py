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

from flask import Flask, jsonify, request

STEP_MINUTES = 30

app = Flask(__name__)

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
}

# Current active scenario
active_scenario = "rising"
custom_pattern = None


def generate_forecast_data(start_time: datetime, num_periods: int = 96) -> List[Dict[str, Any]]:
    """
    Generate forecast data for the specified number of forecast periods.
    
    Args:
        start_time: Starting timestamp for the forecast
        num_periods: Number of periods to generate (default: 96)
        
    Returns:
        List of forecast entries in Carbon Intensity API format
    """
    data = []
    
    # Get the pattern for the active scenario
    if custom_pattern:
        pattern = custom_pattern
        repeat = False
    else:
        scenario = SCENARIOS.get(active_scenario, SCENARIOS["rising"])
        pattern = scenario["pattern"]
        repeat = scenario.get("repeat", True)
    
    # Generate forecast entries
    pattern_length = len(pattern)
    step_minutes = max(1, int(STEP_MINUTES))
    for i in range(num_periods):
        # Get intensity value from pattern (repeat if configured)
        if repeat:
            intensity = pattern[i % pattern_length]
        else:
            intensity = pattern[i] if i < pattern_length else pattern[-1]
        
        # Add some small random variation for realism (±5%)
        import random
        variation = random.uniform(0.95, 1.05)
        intensity = int(intensity * variation)
        
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
        
        data.append({
            "from": start.strftime("%Y-%m-%dT%H:%MZ"),
            "to": end.strftime("%Y-%m-%dT%H:%MZ"),
            "intensity": {
                "forecast": intensity,
                "actual": intensity,  # For mock, actual = forecast
                "index": index
            }
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
        # Parse start time
        if start_time.endswith('Z'):
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


@app.route('/intensity')
def get_current():
    """Return current intensity (first point in forecast)."""
    now = datetime.now(timezone.utc)
    # Floor to 30-minute boundary
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
            "pattern": custom_pattern
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
        custom_pattern = pattern
        return jsonify({
            "status": "scenario updated",
            "scenario": "custom",
            "pattern": custom_pattern
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
            "/health": "Health check"
        },
        "current_scenario": active_scenario if not custom_pattern else "custom",
        "available_scenarios": list(SCENARIOS.keys())
    })


def load_custom_scenario(filepath: str) -> List[int]:
    """Load custom scenario from JSON file."""
    with open(filepath, 'r') as f:
        data = json.load(f)
    
    if isinstance(data, list):
        return data
    elif isinstance(data, dict) and "pattern" in data:
        return data["pattern"]
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
        type=int,
        default=30,
        help='Duration of each forecast slot in minutes (default: 30)'
    )
    
    args = parser.parse_args()
    
    global active_scenario, custom_pattern, STEP_MINUTES
    
    if args.scenario == 'custom':
        if not args.file:
            parser.error("--file required when using --scenario custom")
        custom_pattern = load_custom_scenario(args.file)
        print(f"Loaded custom scenario from {args.file}")
        print(f"Pattern: {custom_pattern}")
    else:
        active_scenario = args.scenario
        scenario = SCENARIOS[active_scenario]
        print(f"Starting with scenario: {scenario['name']}")
        print(f"Description: {scenario['description']}")
        print(f"Pattern: {scenario['pattern']}")
    
    STEP_MINUTES = max(1, args.step_minutes)
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
