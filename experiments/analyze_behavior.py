import pandas as pd
import numpy as np

paths = {
    'credit-greedy': 'experiments/results/simple_20251118_182945_fixed/credit-greedy/timeseries.csv',
    'forecast-aware': 'experiments/results/simple_20251120_221052/forecast-aware/timeseries.csv',
    'forecast-aware-global': 'experiments/results/simple_20251123_095520/forecast-aware-global/timeseries.csv'
}

for name, path in paths.items():
    try:
        print(f"--- {name} ---")
        df = pd.read_csv(path)
        # Assuming 5s intervals.
        # Carbon spikes are usually around specific times. Let's look at the data distribution.
        
        # Create a 'phase' based on carbon intensity to mimic the description in the thesis
        # Low (< 150), High (> 250), etc.
        # Or just look at time segments.
        
        # Let's print mean p100 allocation in different carbon buckets
        df['p100_ratio'] = df['requests_precision_100'] / (df['requests_precision_30'] + df['requests_precision_50'] + df['requests_precision_100'])
        
        print("Average P100 ratio by Carbon Intensity Range:")
        bins = [0, 150, 250, 400]
        labels = ['Low', 'Medium', 'High']
        df['carbon_bucket'] = pd.cut(df['carbon_now'], bins=bins, labels=labels)
        print(df.groupby('carbon_bucket')['p100_ratio'].mean())
        
        # Check behavior before a spike.
        # Identify spikes: where carbon increases significantly.
        df['carbon_change'] = df['carbon_now'].diff()
        
        # Find the start of the major spike (e.g., where carbon jumps from low to high)
        # In the credit-greedy description, it says "When carbon intensity becomes significantly worse (270-300 range)"
        # Let's look at the credit balance evolution.
        
        print("\nCredit Balance Stats:")
        if 'credit_balance' in df.columns:
            print(df['credit_balance'].describe())
        
        # Look at the first 2 minutes (approx 24 samples)
        print("\nFirst 2 mins P100 ratio mean:", df.iloc[:24]['p100_ratio'].mean())
        
        # Look at the spike period (approx sample 30 to 50, need to verify with carbon data)
        # Let's find indices where carbon > 250
        high_carbon_indices = df[df['carbon_now'] > 250].index
        if not high_carbon_indices.empty:
             print(f"\nDuring High Carbon ({len(high_carbon_indices)} samples):")
             print("P100 ratio mean:", df.loc[high_carbon_indices, 'p100_ratio'].mean())
             if 'credit_balance' in df.columns:
                 print("Credit Balance mean:", df.loc[high_carbon_indices, 'credit_balance'].mean())
        
        print("\n")
        
    except Exception as e:
        print(f"Error reading {name}: {e}")
