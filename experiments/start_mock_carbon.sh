#!/usr/bin/env bash
# Start mock carbon API with carbon_scenario.json

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIO_FILE="$SCRIPT_DIR/carbon_scenario.json"
MOCK_API_SCRIPT="$SCRIPT_DIR/../tests/mock-carbon-api.py"

if [[ ! -f "$SCENARIO_FILE" ]]; then
    echo "ERROR: Scenario file not found at $SCENARIO_FILE"
    exit 1
fi

if [[ ! -f "$MOCK_API_SCRIPT" ]]; then
    echo "ERROR: Mock API script not found at $MOCK_API_SCRIPT"
    exit 1
fi

echo "Starting Mock Carbon Intensity API..."
echo "Scenario: $SCENARIO_FILE"
echo "Port: 5001 (as configured in decision-engine)"
echo ""
echo "The decision engine is configured to use: http://host.docker.internal:5001"
echo ""

cd "$(dirname "$MOCK_API_SCRIPT")"
python3 mock-carbon-api.py \
    --scenario custom \
    --file "$SCENARIO_FILE" \
    --step-minutes 1 \
    --port 5001
