#!/bin/bash
# Complete startup script for benchmark system from scratch
# Run this after cluster restart

set -e

echo "════════════════════════════════════════════════════════════════"
echo "🚀 BENCHMARK SYSTEM STARTUP FROM SCRATCH"
echo "════════════════════════════════════════════════════════════════"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Verify pods
echo "📋 Step 1: Verifying pods are running..."
PODS_READY=true

for pod_filter in "app.kubernetes.io/name=carbonstat" "app.kubernetes.io/name=buffer-service-router" "app.kubernetes.io/name=buffer-service-consumer"; do
  count=$(kubectl get pods -n carbonstat -l "$pod_filter" --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "$count" -gt 0 ]; then
    echo "  ✅ Pods with $pod_filter: $count running"
  else
    echo "  ⚠️  No pods running with $pod_filter"
    PODS_READY=false
  fi
done

if ! kubectl get pods -n carbonrouter-system -l app.kubernetes.io/name=decision-engine --no-headers 2>/dev/null | grep -q "Running"; then
  echo "  ⚠️  Decision engine not running"
  PODS_READY=false
else
  echo "  ✅ Decision engine running"
fi

if [ "$PODS_READY" = false ]; then
  echo "  ${RED}⚠️  Some pods not ready. Please wait for cluster to stabilize.${NC}"
fi
echo ""

# Step 2: Kill old port-forwards
echo "🔌 Step 2: Cleaning up old port-forwards..."
for port in 18000 18001 18002 18003 18004; do
  lsof -ti:$port 2>/dev/null | xargs kill -9 2>/dev/null || true
done
echo "  ✅ Old port-forwards cleaned"
echo ""

# Step 3: Establish new port-forwards
echo "🔗 Step 3: Establishing port-forwards..."
kubectl port-forward -n carbonstat svc/buffer-service-router-carbonstat 18000:8000 > /tmp/pf-router.log 2>&1 &
kubectl port-forward -n carbonstat svc/buffer-service-router-carbonstat 18001:8001 > /tmp/pf-router-metrics.log 2>&1 &
kubectl port-forward -n carbonstat svc/buffer-service-consumer-carbonstat 18002:8001 > /tmp/pf-consumer-metrics.log 2>&1 &
kubectl port-forward -n carbonrouter-system svc/carbonrouter-decision-engine 18003:8001 > /tmp/pf-engine-metrics.log 2>&1 &
kubectl port-forward -n carbonrouter-system svc/carbonrouter-decision-engine 18004:80 > /tmp/pf-engine-http.log 2>&1 &

sleep 2

# Verify port-forwards
echo "  Testing port-forwards..."
for port in 18000 18001 18002 18003 18004; do
  if lsof -ti:$port > /dev/null 2>&1; then
    echo "  ✅ Port $port: listening"
  else
    echo "  ${RED}❌ Port $port: NOT listening${NC}"
  fi
done
echo ""

# Step 4: Test port-forward connectivity
echo "🧪 Step 4: Testing port-forward connectivity..."
sleep 1

if curl -s http://localhost:18000/health > /dev/null 2>&1; then
  echo "  ✅ Router HTTP (18000)"
else
  echo "  ${RED}❌ Router HTTP failed${NC}"
fi

if curl -s http://localhost:18001/metrics > /dev/null 2>&1; then
  echo "  ✅ Router metrics (18001)"
else
  echo "  ${RED}❌ Router metrics failed${NC}"
fi

if curl -s http://localhost:18002/metrics > /dev/null 2>&1; then
  echo "  ✅ Consumer metrics (18002)"
else
  echo "  ${RED}❌ Consumer metrics failed${NC}"
fi

if curl -s http://localhost:18003/metrics > /dev/null 2>&1; then
  echo "  ✅ Decision engine metrics (18003)"
else
  echo "  ${RED}❌ Decision engine metrics failed${NC}"
fi

if curl -s http://localhost:18004/healthz > /dev/null 2>&1; then
  echo "  ✅ Decision engine HTTP (18004)"
else
  echo "  ${RED}❌ Decision engine HTTP failed${NC}"
fi
echo ""

# Step 5: Initialize decision engine
echo "⚙️  Step 5: Initializing decision engine (CRITICAL!)..."

# Extract and configure
kubectl get trafficschedule -n carbonstat traffic-schedule -o json | \
  jq '{scheduler: .spec.scheduler, target: .spec.target, consumer: .spec.consumer, router: .spec.router}' > /tmp/ts-config.json

if curl -s -X PUT http://localhost:18004/config/carbonstat/traffic-schedule \
  -H "Content-Type: application/json" \
  -d @/tmp/ts-config.json | grep -q "accepted"; then
  echo "  ✅ Configuration sent to decision engine"
else
  echo "  ${RED}❌ Failed to configure decision engine${NC}"
fi

# Wait for decision engine to process
echo "  Waiting for decision engine to compute schedule..."
sleep 5

# Check for carbon forecast metrics
if curl -s http://localhost:18003/metrics | grep -q 'scheduler_forecast_intensity{horizon="now"'; then
  echo "  ✅ Carbon forecast metrics available"
  CARBON_NOW=$(curl -s http://localhost:18003/metrics | grep 'scheduler_forecast_intensity{horizon="now"' | awk '{print $NF}')
  echo "    - Current carbon: $CARBON_NOW gCO2/kWh"
else
  echo "  ${RED}❌ No carbon forecast metrics found${NC}"
fi
echo ""

# Step 6: Verify end-to-end
echo "🔄 Step 6: Verifying end-to-end routing..."
RESPONSE=$(curl -s -X POST http://localhost:18000/avg \
  -H "Content-Type: application/json" \
  -d '{"numbers":[1,2,3,4,5]}')

if echo "$RESPONSE" | grep -q '"strategy"'; then
  STRATEGY=$(echo "$RESPONSE" | jq -r '.strategy')
  echo "  ✅ Request successful"
  echo "    - Strategy: $STRATEGY"
  echo "    - Response: $RESPONSE"
else
  echo "  ${RED}❌ Request failed${NC}"
  echo "    - Response: $RESPONSE"
fi
echo ""

# Summary
echo "════════════════════════════════════════════════════════════════"
echo "${GREEN}✅ STARTUP COMPLETE!${NC}"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo "  1. Run benchmark:"
echo "     cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/experiments"
echo "     python3 run_simple_benchmark.py --policy credit-greedy"
echo ""
echo "  2. Monitor carbon data:"
echo "     curl -s http://localhost:5001/scenario | jq '.pattern' | head -10"
echo ""
echo "  3. Check decision engine health:"
echo "     curl -s http://localhost:18003/metrics | grep scheduler_forecast | head -5"
echo ""
