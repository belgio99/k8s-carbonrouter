#!/bin/bash
# Setup script for temporal benchmark - ensures all port-forwards are active

echo "Setting up port-forwards for benchmark..."

# Kill any existing port-forwards
pkill -f "kubectl port-forward.*carbonstat" 2>/dev/null
pkill -f "kubectl port-forward.*decision-engine" 2>/dev/null
sleep 2

# Start port-forwards
echo "  → Router (8000 -> 18000)..."
kubectl port-forward -n carbonstat svc/buffer-service-router-carbonstat 18000:8000 > /tmp/pf-router.log 2>&1 &
sleep 1

echo "  → Router metrics (8001 -> 18001)..."
kubectl port-forward -n carbonstat svc/buffer-service-router-carbonstat 18001:8001 > /tmp/pf-router-metrics.log 2>&1 &
sleep 1

echo "  → Consumer metrics (8001 -> 18002)..."
kubectl port-forward -n carbonstat svc/buffer-service-consumer-carbonstat 18002:8001 > /tmp/pf-consumer-metrics.log 2>&1 &
sleep 1

echo "  → Decision engine metrics (8001 -> 18003)..."
kubectl port-forward -n carbonrouter-system svc/carbonrouter-decision-engine 18003:8001 > /tmp/pf-engine-metrics.log 2>&1 &
sleep 2

# Verify all are working
echo ""
echo "Verifying port-forwards..."
failed=0

if curl -s http://127.0.0.1:18000 > /dev/null 2>&1; then
  echo "  ✓ Router (18000)"
else
  echo "  ✗ Router (18000) FAILED"
  failed=1
fi

if curl -s http://127.0.0.1:18001/metrics | head -1 > /dev/null 2>&1; then
  echo "  ✓ Router metrics (18001)"
else
  echo "  ✗ Router metrics (18001) FAILED"
  failed=1
fi

if curl -s http://127.0.0.1:18002/metrics | head -1 > /dev/null 2>&1; then
  echo "  ✓ Consumer metrics (18002)"
else
  echo "  ✗ Consumer metrics (18002) FAILED"
  failed=1
fi

if curl -s http://127.0.0.1:18003/metrics | head -1 > /dev/null 2>&1; then
  echo "  ✓ Decision engine metrics (18003)"
else
  echo "  ✗ Decision engine metrics (18003) FAILED"
  failed=1
fi

echo ""
if [ $failed -eq 0 ]; then
  echo "✓ All port-forwards ready!"
  exit 0
else
  echo "✗ Some port-forwards failed. Check logs in /tmp/pf-*.log"
  exit 1
fi
