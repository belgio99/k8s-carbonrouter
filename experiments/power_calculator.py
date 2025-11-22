"""
Power consumption and carbon emission calculator for infrastructure components.

This module provides utilities to calculate the carbon footprint of the
carbon-aware scheduling system itself, separate from the workload carbon.
"""

import json
from typing import Dict, Tuple
from pathlib import Path


class PowerCalculator:
    """Calculate power consumption and carbon emissions for infrastructure."""

    def __init__(self, power_profiles_path: str = None):
        """
        Initialize the power calculator with power profile data.

        Args:
            power_profiles_path: Path to the power profiles JSON file
                               (defaults to power_profiles.json in same directory as script)
        """
        if power_profiles_path is None:
            # Default: look in the same directory as this script
            script_dir = Path(__file__).parent
            self.profiles_path = script_dir / "power_profiles.json"
        else:
            self.profiles_path = Path(power_profiles_path)

        self.profiles = self._load_profiles()

    def _load_profiles(self) -> Dict:
        """Load power profiles from JSON configuration."""
        if not self.profiles_path.exists():
            raise FileNotFoundError(
                f"Power profiles not found at {self.profiles_path}. "
                "Please ensure power_profiles.json exists in the experiments directory."
            )

        with open(self.profiles_path, 'r') as f:
            return json.load(f)

    def get_always_on_power(self) -> float:
        """
        Calculate total power draw from always-on infrastructure components.

        Returns:
            Total power in Watts
        """
        components = self.profiles['always_on_components']
        total_power = sum(
            comp['power_watts']
            for key, comp in components.items()
            if not key.startswith('_')
        )
        return total_power

    def get_scalable_component_power(
        self,
        component_type: str,
        replica_count: int,
        activity_level: float = 0.5
    ) -> float:
        """
        Calculate power consumption for scalable components.

        Args:
            component_type: Component name (router, consumer, target_precision_X)
            replica_count: Number of running replicas
            activity_level: 0.0 (idle) to 1.0 (fully active), defaults to 0.5

        Returns:
            Total power in Watts for all replicas
        """
        if component_type not in self.profiles['scalable_components']:
            raise ValueError(f"Unknown component type: {component_type}")

        component = self.profiles['scalable_components'][component_type]
        idle_power = component['idle_watts']
        active_power = component['active_watts']

        # Linear interpolation between idle and active power based on activity level
        power_per_replica = idle_power + (active_power - idle_power) * activity_level

        return power_per_replica * replica_count

    def calculate_total_power(
        self,
        router_replicas: int,
        consumer_replicas: int,
        target_replicas_p30: int,
        target_replicas_p50: int,
        target_replicas_p100: int,
        activity_levels: Dict[str, float] = None
    ) -> Tuple[float, Dict[str, float]]:
        """
        Calculate total infrastructure power consumption.

        Args:
            router_replicas: Number of router pods
            consumer_replicas: Number of consumer pods
            target_replicas_p30: Number of precision-30 target pods
            target_replicas_p50: Number of precision-50 target pods
            target_replicas_p100: Number of precision-100 target pods
            activity_levels: Optional dict mapping component type to activity level (0-1)

        Returns:
            Tuple of (total_power_watts, power_breakdown_dict)
        """
        if activity_levels is None:
            activity_levels = {
                'router': 0.5,
                'consumer': 0.5,
                'target_precision_30': 0.5,
                'target_precision_50': 0.5,
                'target_precision_100': 0.5
            }

        breakdown = {}

        # Always-on components
        breakdown['always_on'] = self.get_always_on_power()

        # Scalable components
        breakdown['router'] = self.get_scalable_component_power(
            'router', router_replicas, activity_levels['router']
        )
        breakdown['consumer'] = self.get_scalable_component_power(
            'consumer', consumer_replicas, activity_levels['consumer']
        )
        breakdown['target_p30'] = self.get_scalable_component_power(
            'target_precision_30', target_replicas_p30, activity_levels['target_precision_30']
        )
        breakdown['target_p50'] = self.get_scalable_component_power(
            'target_precision_50', target_replicas_p50, activity_levels['target_precision_50']
        )
        breakdown['target_p100'] = self.get_scalable_component_power(
            'target_precision_100', target_replicas_p100, activity_levels['target_precision_100']
        )

        total_power = sum(breakdown.values())

        return total_power, breakdown

    def power_to_energy(self, power_watts: float, duration_seconds: float) -> float:
        """
        Convert power consumption over time to energy.

        Args:
            power_watts: Power in Watts
            duration_seconds: Time duration in seconds

        Returns:
            Energy in Watt-hours (Wh)
        """
        duration_hours = duration_seconds / 3600.0
        energy_wh = power_watts * duration_hours
        return energy_wh

    def energy_to_carbon(
        self,
        energy_wh: float,
        carbon_intensity_g_per_kwh: float
    ) -> float:
        """
        Convert energy consumption to carbon emissions.

        Args:
            energy_wh: Energy in Watt-hours
            carbon_intensity_g_per_kwh: Carbon intensity in gCO2/kWh

        Returns:
            Carbon emissions in grams CO2
        """
        energy_kwh = energy_wh / 1000.0
        carbon_g = energy_kwh * carbon_intensity_g_per_kwh
        return carbon_g

    def calculate_carbon_emissions(
        self,
        power_watts: float,
        duration_seconds: float,
        carbon_intensity_g_per_kwh: float
    ) -> float:
        """
        Direct calculation from power to carbon emissions.

        Args:
            power_watts: Power consumption in Watts
            duration_seconds: Duration in seconds
            carbon_intensity_g_per_kwh: Carbon intensity in gCO2/kWh

        Returns:
            Carbon emissions in grams CO2
        """
        energy_wh = self.power_to_energy(power_watts, duration_seconds)
        carbon_g = self.energy_to_carbon(energy_wh, carbon_intensity_g_per_kwh)
        return carbon_g

    def calculate_cumulative_carbon(
        self,
        timeseries_data: list,
        power_breakdown_key: str = 'total_power_watts',
        carbon_intensity_key: str = 'carbon_intensity',
        timestamp_key: str = 'timestamp'
    ) -> Tuple[float, list]:
        """
        Calculate cumulative carbon emissions from timeseries data.

        Args:
            timeseries_data: List of dicts with power and carbon intensity samples
            power_breakdown_key: Key for power data in each sample
            carbon_intensity_key: Key for carbon intensity in gCO2/kWh
            timestamp_key: Key for timestamp (for calculating intervals)

        Returns:
            Tuple of (total_carbon_g, cumulative_carbon_timeseries)
        """
        if not timeseries_data:
            return 0.0, []

        cumulative_carbon = []
        total_carbon_g = 0.0

        for i, sample in enumerate(timeseries_data):
            if i == 0:
                # First sample, no interval to calculate
                cumulative_carbon.append({
                    'timestamp': sample[timestamp_key],
                    'cumulative_carbon_g': 0.0
                })
                continue

            # Calculate interval duration
            prev_timestamp = timeseries_data[i-1][timestamp_key]
            curr_timestamp = sample[timestamp_key]

            # Assuming timestamps are in seconds (Unix epoch or elapsed)
            if isinstance(prev_timestamp, str):
                # Parse ISO format if needed
                from datetime import datetime
                prev_dt = datetime.fromisoformat(prev_timestamp.replace('Z', '+00:00'))
                curr_dt = datetime.fromisoformat(curr_timestamp.replace('Z', '+00:00'))
                interval_seconds = (curr_dt - prev_dt).total_seconds()
            else:
                interval_seconds = curr_timestamp - prev_timestamp

            # Get average power and carbon intensity for this interval
            avg_power = (
                timeseries_data[i-1][power_breakdown_key] +
                sample[power_breakdown_key]
            ) / 2.0
            avg_carbon_intensity = (
                timeseries_data[i-1][carbon_intensity_key] +
                sample[carbon_intensity_key]
            ) / 2.0

            # Calculate carbon for this interval
            interval_carbon_g = self.calculate_carbon_emissions(
                avg_power, interval_seconds, avg_carbon_intensity
            )

            total_carbon_g += interval_carbon_g

            cumulative_carbon.append({
                'timestamp': curr_timestamp,
                'cumulative_carbon_g': total_carbon_g,
                'interval_carbon_g': interval_carbon_g
            })

        return total_carbon_g, cumulative_carbon

    def estimate_activity_level(
        self,
        requests_delta: int,
        sample_interval_seconds: float = 5.0,
        requests_per_second_threshold: float = 1.0
    ) -> float:
        """
        Estimate component activity level based on request rate.

        Args:
            requests_delta: Number of requests in sample interval
            sample_interval_seconds: Sample interval duration
            requests_per_second_threshold: RPS above which considered "fully active"

        Returns:
            Activity level between 0.0 (idle) and 1.0 (active)
        """
        if requests_delta <= 0:
            return 0.1  # Minimum activity (pod is running but idle)

        rps = requests_delta / sample_interval_seconds
        activity = min(1.0, rps / requests_per_second_threshold)

        # Ensure minimum activity for running pods
        return max(0.1, activity)

    def print_summary(self, power_breakdown: Dict[str, float]):
        """Print a human-readable summary of power breakdown."""
        print("\n" + "="*60)
        print("POWER CONSUMPTION BREAKDOWN")
        print("="*60)

        print("\nAlways-On Infrastructure:")
        print(f"  Total: {power_breakdown['always_on']:.2f} W")

        print("\nScalable Components:")
        print(f"  Router:         {power_breakdown['router']:.2f} W")
        print(f"  Consumer:       {power_breakdown['consumer']:.2f} W")
        print(f"  Target (p30):   {power_breakdown['target_p30']:.2f} W")
        print(f"  Target (p50):   {power_breakdown['target_p50']:.2f} W")
        print(f"  Target (p100):  {power_breakdown['target_p100']:.2f} W")

        total = sum(power_breakdown.values())
        print(f"\nTotal Power: {total:.2f} W")
        print("="*60 + "\n")


def main():
    """Example usage of PowerCalculator."""
    calc = PowerCalculator()

    # Example: Calculate power for a typical configuration
    power, breakdown = calc.calculate_total_power(
        router_replicas=2,
        consumer_replicas=3,
        target_replicas_p30=1,
        target_replicas_p50=2,
        target_replicas_p100=3
    )

    calc.print_summary(breakdown)

    # Example: Calculate carbon for 10 minutes at 200 gCO2/kWh
    duration_seconds = 600
    carbon_intensity = 200.0

    carbon_g = calc.calculate_carbon_emissions(power, duration_seconds, carbon_intensity)

    print(f"Example Calculation:")
    print(f"  Duration: {duration_seconds/60:.1f} minutes")
    print(f"  Carbon Intensity: {carbon_intensity:.1f} gCO2/kWh")
    print(f"  Total Infrastructure Carbon: {carbon_g:.2f} gCO2")
    print(f"  Equivalent to: {carbon_g/1000:.4f} kgCO2\n")


if __name__ == '__main__':
    main()
