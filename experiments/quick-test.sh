#!/usr/bin/env bash
# Quick Start Guide for Manual Carbon Intensity Testing
#
# This script provides a quick menu-driven interface for testing
# different carbon intensity scenarios

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

print_header() {
    echo ""
    echo -e "${BLUE}============================================${NC}"
    echo -e "${BLUE}  Carbon Intensity Manual Testing Tool${NC}"
    echo -e "${BLUE}============================================${NC}"
    echo ""
}

show_menu() {
    echo "Choose a testing method:"
    echo ""
    echo "1) Start Mock Carbon API Server"
    echo "2) Use Manual Schedule Override (API)"
    echo "3) Run Automated Scenario Tests"
    echo "4) Quick Test - Rising Intensity"
    echo "5) Quick Test - Falling Intensity"
    echo "6) Quick Test - Peak Intensity"
    echo "7) Quick Test - Clean Period"
    echo "8) Load Custom Scenario File"
    echo "9) Exit"
    echo ""
}

start_mock_api() {
    echo -e "${GREEN}Starting Mock Carbon API Server...${NC}"
    echo ""
    echo "Available scenarios:"
    echo "  - rising (default)"
    echo "  - peak"
    echo "  - falling"
    echo "  - low"
    echo "  - volatile"
    echo "  - stable"
    echo ""
    read -p "Choose scenario [rising]: " scenario
    scenario=${scenario:-rising}
    
    read -p "Port [5000]: " port
    port=${port:-5000}
    
    echo ""
    echo -e "${GREEN}Starting mock API with scenario: $scenario${NC}"
    echo "Press Ctrl+C to stop"
    echo ""
    
    cd "$SCRIPT_DIR"
    python3 mock-carbon-api.py --scenario "$scenario" --port "$port"
}

manual_override() {
    echo -e "${GREEN}Manual Schedule Override${NC}"
    echo ""
    read -p "Decision Engine URL [http://localhost:8080]: " url
    url=${url:-http://localhost:8080}
    
    read -p "Namespace [default]: " namespace
    namespace=${namespace:-default}
    
    read -p "Schedule Name [test-schedule]: " name
    name=${name:-test-schedule}
    
    echo ""
    read -p "Carbon intensity NOW (gCO2/kWh) [150]: " now
    now=${now:-150}
    
    read -p "Carbon intensity NEXT (gCO2/kWh) [220]: " next
    next=${next:-220}
    
    # Calculate valid until
    if [[ "$OSTYPE" == "darwin"* ]]; then
        valid_until=$(date -u -v+10M +%Y-%m-%dT%H:%M:%SZ)
    else
        valid_until=$(date -u -d '+10 minutes' +%Y-%m-%dT%H:%M:%SZ)
    fi
    
    echo ""
    echo -e "${YELLOW}Setting manual schedule:${NC}"
    echo "  URL: $url"
    echo "  Namespace: $namespace"
    echo "  Name: $name"
    echo "  Carbon now: $now gCO2/kWh"
    echo "  Carbon next: $next gCO2/kWh"
    echo "  Valid until: $valid_until"
    echo ""
    
    read -p "Proceed? [Y/n]: " confirm
    confirm=${confirm:-Y}
    
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        curl -X POST "$url/schedule/$namespace/$name/manual" \
            -H "Content-Type: application/json" \
            -d "{
                \"carbonForecastNow\": $now,
                \"carbonForecastNext\": $next,
                \"validUntil\": \"$valid_until\"
            }"
        echo ""
        echo ""
        echo -e "${GREEN}✓ Manual schedule set successfully${NC}"
        echo ""
        echo "View results:"
        echo "  curl $url/schedule/$namespace/$name | jq"
    fi
}

run_automated_tests() {
    echo -e "${GREEN}Running Automated Scenario Tests${NC}"
    echo ""
    
    read -p "Decision Engine URL [http://localhost:8080]: " url
    url=${url:-http://localhost:8080}
    
    read -p "Namespace [default]: " namespace
    namespace=${namespace:-default}
    
    read -p "Schedule Name [test-schedule]: " name
    name=${name:-test-schedule}
    
    echo ""
    echo "Starting tests..."
    echo ""
    
    DECISION_ENGINE="$url" \
    NAMESPACE="$namespace" \
    SCHEDULE_NAME="$name" \
    "$SCRIPT_DIR/test-carbon-scenarios.sh"
}

quick_test() {
    local scenario=$1
    local now=$2
    local next=$3
    local description=$4
    
    echo -e "${GREEN}Quick Test: $description${NC}"
    echo ""
    
    read -p "Decision Engine URL [http://localhost:8080]: " url
    url=${url:-http://localhost:8080}
    
    read -p "Namespace [default]: " namespace
    namespace=${namespace:-default}
    
    read -p "Schedule Name [test-schedule]: " name
    name=${name:-test-schedule}
    
    if [[ "$OSTYPE" == "darwin"* ]]; then
        valid_until=$(date -u -v+10M +%Y-%m-%dT%H:%M:%SZ)
    else
        valid_until=$(date -u -d '+10 minutes' +%Y-%m-%dT%H:%M:%SZ)
    fi
    
    echo ""
    echo "Setting scenario: $description"
    echo "  Carbon now: $now gCO2/kWh"
    echo "  Carbon next: $next gCO2/kWh"
    echo ""
    
    curl -s -X POST "$url/schedule/$namespace/$name/manual" \
        -H "Content-Type: application/json" \
        -d "{
            \"carbonForecastNow\": $now,
            \"carbonForecastNext\": $next,
            \"validUntil\": \"$valid_until\"
        }" > /dev/null
    
    echo -e "${GREEN}✓ Scenario set${NC}"
    echo ""
    echo "Waiting 3 seconds for update..."
    sleep 3
    
    echo ""
    echo "Results:"
    curl -s "$url/schedule/$namespace/$name" | jq '{
        policy: .activePolicy,
        carbon: {now: .carbonForecastNow, next: .carbonForecastNext},
        carbon_adjustment: .diagnostics.carbon_adjustment,
        total_adjustment: .diagnostics.total_adjustment,
        throttle: .processing.throttle,
        flavours: .flavours | map({name: .name, weight: (.weight * 100 | round)})
    }'
    
    echo ""
    read -p "Press Enter to continue..."
}

load_custom_scenario() {
    echo -e "${GREEN}Load Custom Scenario File${NC}"
    echo ""
    echo "Available scenario files in experiments/scenarios/:"
    echo ""
    
    if [ -d "$SCRIPT_DIR/scenarios" ]; then
        ls -1 "$SCRIPT_DIR/scenarios/"*.json 2>/dev/null | xargs -n1 basename || echo "  (no files found)"
    else
        echo "  (scenarios directory not found)"
    fi
    
    echo ""
    read -p "Enter scenario file name (or full path): " filename
    
    if [ -z "$filename" ]; then
        echo "No file specified"
        return
    fi
    
    # Check if it's a full path or just a filename
    if [ -f "$filename" ]; then
        filepath="$filename"
    elif [ -f "$SCRIPT_DIR/scenarios/$filename" ]; then
        filepath="$SCRIPT_DIR/scenarios/$filename"
    else
        echo "File not found: $filename"
        return
    fi
    
    echo ""
    echo "Loading scenario from: $filepath"
    echo ""
    
    read -p "Port for mock API [5000]: " port
    port=${port:-5000}
    
    echo ""
    echo -e "${GREEN}Starting mock API with custom scenario${NC}"
    echo "Press Ctrl+C to stop"
    echo ""
    
    cd "$SCRIPT_DIR"
    python3 mock-carbon-api.py --scenario custom --file "$filepath" --port "$port"
}

main() {
    print_header
    
    # Check requirements
    for tool in curl jq python3; do
        if ! command -v $tool &> /dev/null; then
            echo "Error: Required tool '$tool' not found"
            echo "Please install: $tool"
            exit 1
        fi
    done
    
    while true; do
        show_menu
        read -p "Select option [1-9]: " choice
        
        case $choice in
            1)
                start_mock_api
                ;;
            2)
                manual_override
                ;;
            3)
                run_automated_tests
                ;;
            4)
                quick_test "rising" 120 280 "Rising Intensity (Morning)"
                ;;
            5)
                quick_test "falling" 280 120 "Falling Intensity (Evening)"
                ;;
            6)
                quick_test "peak" 350 340 "Peak Intensity (Midday)"
                ;;
            7)
                quick_test "clean" 40 35 "Clean Period (Night)"
                ;;
            8)
                load_custom_scenario
                ;;
            9)
                echo "Exiting..."
                exit 0
                ;;
            *)
                echo "Invalid option"
                ;;
        esac
        
        echo ""
        read -p "Press Enter to return to menu..."
    done
}

main
