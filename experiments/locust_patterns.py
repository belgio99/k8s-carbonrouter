"""Custom Locust load shapes for predictable demand patterns."""

from locust import LoadTestShape


class SawtoothLoadShape(LoadTestShape):
    """
    Sawtooth pattern: Ramp up linearly, then sudden drop.

    Pattern repeats every cycle_duration seconds:
    - Ramps from min_users to max_users over ramp_duration
    - Drops back to min_users
    - Repeats

    This creates predictable spikes that DemandEstimator can forecast.
    """

    # Pattern configuration
    min_users = 50
    max_users = 200
    cycle_duration = 180  # 3 minutes per cycle
    ramp_duration = 120   # 2 minutes ramp up, 1 minute at peak, then drop
    spawn_rate = 20

    def tick(self):
        run_time = self.get_run_time()

        # Calculate position in current cycle
        cycle_position = run_time % self.cycle_duration

        if cycle_position < self.ramp_duration:
            # Ramping up phase
            progress = cycle_position / self.ramp_duration
            user_count = int(self.min_users + (self.max_users - self.min_users) * progress)
        else:
            # At peak or dropping back to min
            user_count = self.min_users

        return user_count, self.spawn_rate


class SineWaveLoadShape(LoadTestShape):
    """
    Sine wave pattern: Smooth oscillation between min and max users.

    Creates a sinusoidal load pattern that's easy to predict.
    Period = 240 seconds (4 minutes) by default.
    """

    min_users = 80
    max_users = 160
    period = 240  # 4 minutes
    spawn_rate = 15

    def tick(self):
        import math
        run_time = self.get_run_time()

        # Sine wave: oscillates between -1 and +1
        phase = (2 * math.pi * run_time) / self.period
        sine_value = math.sin(phase)

        # Map from [-1, +1] to [min_users, max_users]
        amplitude = (self.max_users - self.min_users) / 2
        midpoint = (self.max_users + self.min_users) / 2
        user_count = int(midpoint + amplitude * sine_value)

        return user_count, self.spawn_rate


class StepFunctionLoadShape(LoadTestShape):
    """
    Step function: Alternates between low and high load at fixed intervals.

    Pattern:
    - 2 minutes at low load (50 users)
    - 1 minute at high load (200 users)
    - Repeat

    Very predictable for demand forecasting.
    """

    low_users = 50
    high_users = 200
    low_duration = 120   # 2 minutes
    high_duration = 60   # 1 minute
    spawn_rate = 25

    def tick(self):
        run_time = self.get_run_time()
        cycle_duration = self.low_duration + self.high_duration
        cycle_position = run_time % cycle_duration

        if cycle_position < self.low_duration:
            user_count = self.low_users
        else:
            user_count = self.high_users

        return user_count, self.spawn_rate


class ScheduledSpikesLoadShape(LoadTestShape):
    """
    Scheduled spikes: Baseline load with predictable spikes.

    Spikes occur at fixed intervals (every 180 seconds).
    Spike duration: 30 seconds
    This allows DemandEstimator to predict "spike coming in 30 seconds".
    """

    baseline_users = 100
    spike_users = 250
    spike_interval = 180  # Spike every 3 minutes
    spike_duration = 30   # Spike lasts 30 seconds
    spawn_rate = 30

    def tick(self):
        run_time = self.get_run_time()

        # Are we in a spike window?
        time_since_last_spike = run_time % self.spike_interval

        if time_since_last_spike < self.spike_duration:
            # In spike
            user_count = self.spike_users
        else:
            # Baseline
            user_count = self.baseline_users

        return user_count, self.spawn_rate
