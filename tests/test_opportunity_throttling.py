import sys
import os
import unittest
from datetime import datetime, timedelta
# Add decision-engine directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../decision-engine')))

from scheduler.models import ScalingDirective, SchedulerConfig, ForecastSnapshot, ForecastPoint

class TestOpportunityThrottling(unittest.TestCase):
    def test_opportunity_throttling(self):
        """Verify throttling is more aggressive when future is greener"""
        config = SchedulerConfig(throttle_min=0.05)
        
        # Case 1: Flat forecast (No opportunity)
        # Current = 200, Future = 200
        forecast_flat = ForecastSnapshot(
            intensity_now=200.0,
            intensity_next=200.0,
            schedule=[
                ForecastPoint(start=datetime.utcnow(), end=datetime.utcnow(), forecast=200.0)
                for _ in range(6)
            ]
        )
        
        directive_flat = ScalingDirective.from_state(
            credit_balance=0.0,
            config=config,
            forecast=forecast_flat
        )
        
        # Case 2: Great opportunity
        # Current = 200, Future = 100 (Ratio 2.0)
        forecast_opportunity = ForecastSnapshot(
            intensity_now=200.0,
            intensity_next=200.0,
            schedule=[
                ForecastPoint(start=datetime.utcnow(), end=datetime.utcnow(), forecast=100.0)
                for _ in range(6)
            ]
        )
        
        directive_opportunity = ScalingDirective.from_state(
            credit_balance=0.0,
            config=config,
            forecast=forecast_opportunity
        )
        
        print(f"Flat Throttle: {directive_flat.throttle}")
        print(f"Opportunity Throttle: {directive_opportunity.throttle}")
        
        self.assertLess(directive_opportunity.throttle, directive_flat.throttle, 
                        "Throttle should be lower when opportunity exists")
        
        # Check if it scaled roughly by 1/ratio (0.5)
        # Note: The base throttle might be influenced by intensity_ratio, so we check for significant drop
        self.assertLess(directive_opportunity.throttle, directive_flat.throttle * 0.6,
                        "Throttle should drop significantly (approx 50%)")

if __name__ == '__main__':
    unittest.main()
