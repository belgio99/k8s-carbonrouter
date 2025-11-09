#!/bin/bash
# Initialize decision engine with TrafficSchedule configuration
# Run this after restarting the decision engine

set -e

echo "🔧 Initializing decision engine..."

# Extract TrafficSchedule config
kubectl get trafficschedule -n carbonstat traffic-schedule -o json | \
  jq '{scheduler: .spec.scheduler, target: .spec.target, consumer: .spec.consumer, router: .spec.router}' > /tmp/ts-config.json

# Configure decision engine
curl -s -X PUT http://localhost:18004/config/carbonstat/traffic-schedule \
  -H "Content-Type: application/json" \
  -d @/tmp/ts-config.json

echo ""
echo "✅ Decision engine configured!"
echo ""
echo "Checking carbon forecast metrics..."
sleep 3
curl -s http://localhost:18003/metrics | grep 'scheduler_forecast_intensity{horizon="now"' || echo "⚠️  No metrics yet, wait a few seconds"
