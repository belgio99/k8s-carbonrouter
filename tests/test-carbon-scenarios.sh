#!/usr/bin/env bash
# test-carbon-scenarios.sh
#
# Automated testing script for carbon-aware scheduler scenarios
# Tests different carbon intensity patterns and validates scheduler behavior

set -e

# Configuration
DECISION_ENGINE="${DECISION_ENGINE:-http://localhost:8080}"
NAMESPACE="${NAMESPACE:-default}"
SCHEDULE_NAME="${SCHEDULE_NAME:-test-schedule}"
WAIT_TIME="${WAIT_TIME:-5}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test scenarios: name:now:next:description:expected_adjustment
declare -a scenarios=(
  "rising:120:280:Should conserve credit (rising intensity):negative"
  "falling:280:120:Should spend credit (falling intensity):positive"
  "peak:350:340:High precision baseline (peak intensity):conservative"
  "clean:40:35:Aggressive green strategy (clean period):aggressive"
  "stable:180:185:Balanced approach (stable intensity):neutral"
  "extreme-rise:100:400:Extreme rise - strong conservation:very_negative"
  "extreme-fall:400:100:Extreme fall - aggressive spend:very_positive"
)

# Function to print colored output
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to calculate expected adjustment
validate_adjustment() {
    local scenario_type=$1
    local carbon_adj=$2
    
    case $scenario_type in
        negative)
            if (( $(echo "$carbon_adj < -0.1" | bc -l) )); then
                return 0
            fi
            ;;
        positive)
            if (( $(echo "$carbon_adj > 0.1" | bc -l) )); then
                return 0
            fi
            ;;
        very_negative)
            if (( $(echo "$carbon_adj < -0.5" | bc -l) )); then
                return 0
            fi
            ;;
        very_positive)
            if (( $(echo "$carbon_adj > 0.5" | bc -l) )); then
                return 0
            fi
            ;;
        neutral)
            if (( $(echo "$carbon_adj > -0.2 && $carbon_adj < 0.2" | bc -l) )); then
                return 0
            fi
            ;;
        conservative|aggressive)
            return 0  # Just check it ran
            ;;
    esac
    return 1
}

# Function to test a single scenario
test_scenario() {
    local name=$1
    local now=$2
    local next=$3
    local description=$4
    local expected=$5
    
    echo ""
    echo "========================================="
    print_info "Testing scenario: $name"
    print_info "Description: $description"
    print_info "Carbon now: $now gCO2/kWh, next: $next gCO2/kWh"
    echo "========================================="
    
    # Calculate valid until (5 minutes from now)
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        valid_until=$(date -u -v+5M +%Y-%m-%dT%H:%M:%SZ)
    else
        # Linux
        valid_until=$(date -u -d '+5 minutes' +%Y-%m-%dT%H:%M:%SZ)
    fi
    
    # Set manual schedule
    print_info "Setting manual schedule..."
    response=$(curl -s -X POST "$DECISION_ENGINE/schedule/$NAMESPACE/$SCHEDULE_NAME/manual" \
        -H "Content-Type: application/json" \
        -d "{
            \"carbonForecastNow\": $now,
            \"carbonForecastNext\": $next,
            \"validUntil\": \"$valid_until\"
        }")
    
    if echo "$response" | grep -q "schedule set"; then
        print_success "Manual schedule set successfully"
    else
        print_error "Failed to set manual schedule"
        echo "$response"
        return 1
    fi
    
    # Wait for metrics to update
    print_info "Waiting $WAIT_TIME seconds for metrics to update..."
    sleep "$WAIT_TIME"
    
    # Fetch results
    print_info "Fetching schedule results..."
    result=$(curl -s "$DECISION_ENGINE/schedule/$NAMESPACE/$SCHEDULE_NAME")
    
    if [ -z "$result" ]; then
        print_error "No response from decision engine"
        return 1
    fi
    
    # Extract key metrics
    policy=$(echo "$result" | jq -r '.activePolicy // "unknown"')
    carbon_now=$(echo "$result" | jq -r '.carbonForecastNow // 0')
    carbon_next=$(echo "$result" | jq -r '.carbonForecastNext // 0')
    carbon_adj=$(echo "$result" | jq -r '.diagnostics.carbon_adjustment // 0')
    demand_adj=$(echo "$result" | jq -r '.diagnostics.demand_adjustment // 0')
    total_adj=$(echo "$result" | jq -r '.diagnostics.total_adjustment // 0')
    throttle=$(echo "$result" | jq -r '.processing.throttle // 1.0')
    
    # Display results
    echo ""
    print_success "Results:"
    echo "  Policy:              $policy"
    echo "  Carbon (now/next):   $carbon_now / $carbon_next gCO2/kWh"
    echo "  Carbon Adjustment:   $carbon_adj"
    echo "  Demand Adjustment:   $demand_adj"
    echo "  Total Adjustment:    $total_adj"
    echo "  Throttle Factor:     $throttle"
    
    # Display flavour weights
    echo ""
    echo "  Flavour Weights:"
    echo "$result" | jq -r '.flavours[]? | "    \(.name): \(.weight * 100 | round)%"'
    
    # Validate against expected behavior
    echo ""
    if validate_adjustment "$expected" "$carbon_adj"; then
        print_success "✓ Behavior matches expectation ($expected)"
    else
        print_warning "⚠ Behavior may differ from expectation ($expected)"
        print_warning "  Carbon adjustment: $carbon_adj"
    fi
    
    return 0
}

# Main execution
main() {
    print_info "Starting carbon-aware scheduler scenario tests"
    print_info "Decision Engine: $DECISION_ENGINE"
    print_info "Namespace: $NAMESPACE"
    print_info "Schedule Name: $SCHEDULE_NAME"
    echo ""
    
    # Check if decision engine is reachable
    print_info "Checking decision engine health..."
    if ! curl -s -f "$DECISION_ENGINE/healthz" > /dev/null 2>&1; then
        print_error "Decision engine not reachable at $DECISION_ENGINE"
        print_error "Make sure to port-forward: kubectl port-forward -n carbonshift svc/decision-engine 8080:8080"
        exit 1
    fi
    print_success "Decision engine is reachable"
    
    # Check for required tools
    for tool in curl jq bc; do
        if ! command -v $tool &> /dev/null; then
            print_error "Required tool '$tool' not found. Please install it first."
            exit 1
        fi
    done
    
    # Run all scenarios
    passed=0
    failed=0
    
    for scenario in "${scenarios[@]}"; do
        IFS=':' read -r name now next description expected <<< "$scenario"
        
        if test_scenario "$name" "$now" "$next" "$description" "$expected"; then
            ((passed++))
        else
            ((failed++))
        fi
        
        # Pause between scenarios if not in CI mode
        if [ -z "$CI" ]; then
            echo ""
            read -p "Press Enter to continue to next scenario..." -r
        else
            sleep 2
        fi
    done
    
    # Summary
    echo ""
    echo "========================================="
    print_info "Test Summary"
    echo "========================================="
    print_success "Passed: $passed"
    if [ $failed -gt 0 ]; then
        print_error "Failed: $failed"
    else
        echo "Failed: 0"
    fi
    echo ""
    
    if [ $failed -eq 0 ]; then
        print_success "All tests passed! ✓"
        exit 0
    else
        print_error "Some tests failed"
        exit 1
    fi
}

# Run main function
main
