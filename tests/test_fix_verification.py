import sys
import os
import unittest
from unittest.mock import MagicMock
# Add decision-engine directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../decision-engine')))

from scheduler.models import FlavourProfile, ForecastSnapshot, SchedulerConfig
from scheduler.strategies.forecast_aware_global import ForecastAwareGlobalPolicy
from scheduler.ledger import CreditLedger

class TestFixVerification(unittest.TestCase):
    def test_throttle_min_default(self):
        """Verify throttle_min default is 0.05"""
        config = SchedulerConfig()
        self.assertEqual(config.throttle_min, 0.05, "throttle_min should be 0.05")

    def test_emissions_calculation(self):
        """Verify emissions calculation scales with grid intensity"""
        ledger = MagicMock(spec=CreditLedger)
        ledger.balance = 0.0
        ledger.credit_min = -1.0
        ledger.credit_max = 1.0
        
        policy = ForecastAwareGlobalPolicy(ledger)
        
        flavours = [
            FlavourProfile(name="p100", precision=1.0, carbon_intensity=1.0),
            FlavourProfile(name="p50", precision=0.5, carbon_intensity=0.5)
        ]
        
        # Test with high intensity
        forecast_high = ForecastSnapshot(intensity_now=300.0, intensity_next=300.0)
        policy.evaluate(flavours, forecast_high)
        
        # Check cumulative carbon (should be ~1.0 * 300 = 300)
        # Since evaluate calls super().evaluate which returns p100 weight ~1.0 initially
        # The cumulative carbon should be roughly 300, not 1.0
        self.assertGreater(policy._cumulative_carbon, 100.0, "Cumulative carbon should reflect grid intensity")
        
        # Test with low intensity
        policy.reset_cumulative_emissions()
        forecast_low = ForecastSnapshot(intensity_now=50.0, intensity_next=50.0)
        policy.evaluate(flavours, forecast_low)
        
        self.assertLess(policy._cumulative_carbon, 100.0, "Cumulative carbon should be lower for low intensity")
        self.assertGreater(policy._cumulative_carbon, 10.0, "Cumulative carbon should not be zero")

if __name__ == '__main__':
    unittest.main()
