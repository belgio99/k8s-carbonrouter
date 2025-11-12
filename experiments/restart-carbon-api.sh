#!/bin/bash
# Restart Carbon API with the latest code
# This ensures the API has all latest endpoints (like /reset)

set -e

echo "🔄 Restarting Carbon API..."

# Kill any existing carbon API process
echo "  → Stopping old process..."
pkill -f mock-carbon-api || echo "  → No existing process found"

sleep 1

# Start fresh carbon API with custom scenario
echo "  → Starting fresh process..."
cd "$(dirname "$0")/../tests"
python3 mock-carbon-api.py \
    --scenario custom \
    --file ../experiments/carbon_scenario.json \
    --port 5001 \
    > /tmp/carbon-api.log 2>&1 &

CARBON_PID=$!
echo "  → Started with PID: $CARBON_PID"

sleep 2

# Verify it's running
if ps -p $CARBON_PID > /dev/null; then
    echo "✅ Carbon API is running"

    # Test the health endpoint
    if curl -s http://localhost:5001/health | grep -q "ok"; then
        echo "✅ Health check passed"

        # Test the reset endpoint
        if curl -s -X POST http://localhost:5001/reset | grep -q "scenario reset"; then
            echo "✅ Reset endpoint working"
            echo ""
            echo "Carbon API ready! Logs: /tmp/carbon-api.log"
        else
            echo "⚠️  Warning: Reset endpoint not responding"
        fi
    else
        echo "⚠️  Warning: Health check failed"
    fi
else
    echo "❌ Failed to start Carbon API"
    echo "Check logs: /tmp/carbon-api.log"
    exit 1
fi
