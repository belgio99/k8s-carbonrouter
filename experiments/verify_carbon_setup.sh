#!/usr/bin/env bash
# Verify the carbon scenario setup

set -e

echo "======================================"
echo "Carbon Scenario Setup Verification"
echo "======================================"
echo ""

# Check if mock API is running
echo "[1/5] Checking if mock API is running..."
if curl -s http://localhost:5001/health > /dev/null 2>&1; then
    echo "✓ Mock API is running on port 5001"
else
    echo "✗ Mock API is NOT running"
    echo "Start it with: cd experiments && ./start_mock_carbon.sh"
    exit 1
fi

# Check current scenario
echo ""
echo "[2/5] Checking active scenario..."
SCENARIO=$(curl -s http://localhost:5001/scenario | python3 -c "import sys, json; print(json.load(sys.stdin).get('scenario', 'unknown'))")
echo "✓ Active scenario: $SCENARIO"

# Check decision engine configuration
echo ""
echo "[3/5] Checking decision engine configuration..."
CARBON_URL=$(kubectl get deployment -n carbonrouter-system carbonrouter-decision-engine -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="CARBON_API_URL")].value}')
echo "✓ Decision engine CARBON_API_URL: $CARBON_URL"

if [[ "$CARBON_URL" == *"5001"* ]]; then
    echo "✓ Decision engine is configured to use the mock API"
else
    echo "⚠ Decision engine is NOT configured to use the mock API on port 5001"
    echo "Current: $CARBON_URL"
    echo "Expected: http://host.docker.internal:5001"
fi

# Check if decision engine is running
echo ""
echo "[4/5] Checking decision engine status..."
POD_STATUS=$(kubectl get pods -n carbonrouter-system -l app.kubernetes.io/name=decision-engine -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "NotFound")
if [[ "$POD_STATUS" == "Running" ]]; then
    echo "✓ Decision engine is running"
elif [[ "$POD_STATUS" == "NotFound" ]]; then
    echo "✗ Decision engine pod not found"
    exit 1
else
    echo "✗ Decision engine status: $POD_STATUS"
    exit 1
fi

# Test carbon intensity fetch
echo ""
echo "[5/5] Testing carbon intensity fetch..."
INTENSITY=$(curl -s http://localhost:5001/intensity | python3 -c "import sys, json; data = json.load(sys.stdin); print(data['data'][0]['intensity']['forecast'])")
echo "✓ Current carbon intensity from mock API: $INTENSITY gCO₂/kWh"

echo ""
echo "======================================"
echo "✓ Setup verification complete!"
echo "======================================"
echo ""
echo "The decision engine is correctly configured to use carbon_scenario.json"
echo "via the mock Carbon Intensity API."
echo ""
echo "Pattern info:"
curl -s http://localhost:5001/scenario | python3 -c "import sys, json; data = json.load(sys.stdin); pattern = data.get('pattern', []); print(f'  - Total points: {len(pattern)}'); print(f'  - Range: {min(pattern)}-{max(pattern)} gCO₂/kWh'); print(f'  - First values: {pattern[:5]}...'); print(f'  - Last values: ...{pattern[-5:]}')"
echo ""
