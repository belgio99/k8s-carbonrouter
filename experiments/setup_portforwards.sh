#!/bin/bash
# Robust port-forward setup script
# This script ensures all required port-forwards are running and can be called multiple times safely

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="/tmp/k8s-portforward-logs"
mkdir -p "$LOG_DIR"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "═══════════════════════════════════════════════════════════"
echo "  Robust Port-Forward Setup"
echo "═══════════════════════════════════════════════════════════"
echo ""

# Function to kill all existing port-forwards
kill_existing_portforwards() {
    echo "→ Killing existing port-forwards..."
    pkill -f "kubectl port-forward" 2>/dev/null || true
    sleep 2
    echo "  ✓ Cleaned up old port-forwards"
}

# Function to start a single port-forward
start_portforward() {
    local namespace=$1
    local service=$2
    local local_port=$3
    local remote_port=$4
    local name=$5
    local logfile="$LOG_DIR/pf-${name}.log"
    
    echo "→ Starting: $name (localhost:${local_port} -> ${service}:${remote_port})"
    
    # Start port-forward in background
    kubectl port-forward -n "$namespace" "svc/$service" "${local_port}:${remote_port}" \
        > "$logfile" 2>&1 &
    
    local pid=$!
    sleep 2
    
    # Check if it's still running
    if ps -p $pid > /dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} PID: $pid"
        return 0
    else
        echo -e "  ${RED}✗${NC} Failed to start (check $logfile)"
        return 1
    fi
}

# Function to verify a port-forward is accessible
verify_endpoint() {
    local url=$1
    local name=$2
    local max_retries=10
    local retry=0
    
    while [ $retry -lt $max_retries ]; do
        if curl -s --max-time 2 "$url" > /dev/null 2>&1; then
            echo -e "  ${GREEN}✓${NC} $name is accessible"
            return 0
        fi
        retry=$((retry + 1))
        sleep 1
    done
    
    echo -e "  ${YELLOW}⚠${NC} $name not responding (might be starting up)"
    return 1
}

# Main execution
main() {
    # Step 1: Clean up
    kill_existing_portforwards
    echo ""
    
    # Step 2: Start all port-forwards
    echo "Starting port-forwards..."
    echo ""
    
    local failed=0
    
    # Router service (2 ports from same service - use multi-port syntax)
    echo "→ Starting: router (localhost:18000,18001 -> buffer-service-router-carbonstat:8000,8001)"
    kubectl port-forward -n carbonstat svc/buffer-service-router-carbonstat 18000:8000 18001:8001 \
        > "$LOG_DIR/pf-router.log" 2>&1 &
    ROUTER_PID=$!
    sleep 2
    if ps -p $ROUTER_PID > /dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} PID: $ROUTER_PID"
    else
        echo -e "  ${RED}✗${NC} Failed to start (check $LOG_DIR/pf-router.log)"
        failed=1
    fi
    
    # Consumer metrics
    start_portforward "carbonstat" "buffer-service-consumer-carbonstat" 18002 8001 "consumer-metrics" || failed=1
    
    # Decision engine (2 ports from same service - use multi-port syntax)
    echo "→ Starting: engine (localhost:18003,18004 -> carbonrouter-decision-engine:8001,80)"
    kubectl port-forward -n carbonrouter-system svc/carbonrouter-decision-engine 18003:8001 18004:80 \
        > "$LOG_DIR/pf-engine.log" 2>&1 &
    ENGINE_PID=$!
    sleep 2
    if ps -p $ENGINE_PID > /dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} PID: $ENGINE_PID"
    else
        echo -e "  ${RED}✗${NC} Failed to start (check $LOG_DIR/pf-engine.log)"
        failed=1
    fi
    
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  Verification"
    echo "═══════════════════════════════════════════════════════════"
    echo ""
    
    # Step 3: Verify endpoints
    verify_endpoint "http://127.0.0.1:18001/metrics" "Router Metrics (18001)"
    verify_endpoint "http://127.0.0.1:18002/metrics" "Consumer Metrics (18002)"
    verify_endpoint "http://127.0.0.1:18003/metrics" "Engine Metrics (18003)"
    verify_endpoint "http://127.0.0.1:18004/schedule" "Engine API (18004)"
    
    echo ""
    
    if [ $failed -eq 0 ]; then
        echo -e "${GREEN}✓ All port-forwards are running!${NC}"
        echo ""
        echo "Logs are in: $LOG_DIR"
        echo "To monitor: tail -f $LOG_DIR/pf-*.log"
        return 0
    else
        echo -e "${RED}✗ Some port-forwards failed to start${NC}"
        echo "Check logs in: $LOG_DIR"
        return 1
    fi
}

# Run main function
main
