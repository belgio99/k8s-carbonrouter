import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import json
from pathlib import Path
from math import pi

# Setup
sns.set_style('whitegrid')
plt.rcParams['figure.figsize'] = (12, 8)
plt.rcParams['font.size'] = 12
OUTPUT_DIR = '../docs/thesis/images/validation'

# Constants
ENERGY_P30 = 0.30
ENERGY_P50 = 0.50
ENERGY_P100 = 1.00

# Data Paths
data_paths = {
    'p100': 'results/simple_20251120_185352/p100/timeseries.csv',
    'round-robin': 'results/simple_20251120_190537/round-robin/timeseries.csv',
    'random': 'results/simple_20251120_191721/random/timeseries.csv',
    'credit-greedy': 'results/simple_20251118_182945_fixed/credit-greedy/timeseries.csv',
    'forecast-aware': 'results/simple_20251120_221052/forecast-aware/timeseries.csv',
    'forecast-aware-global': 'results/simple_20251123_095520/forecast-aware-global/timeseries.csv',
}

# Load Data
strategies = {}
# Mock demand pattern
try:
    with open('demand_scenario.json', 'r') as f:
        demand_config = json.load(f)
    demand_pattern = demand_config['pattern']
    max_demand = max(demand_pattern)
except:
    print("Warning: demand_scenario.json not found, using placeholder.")
    demand_pattern = [100] * 120
    max_demand = 100

for name, path in data_paths.items():
    try:
        df = pd.read_csv(path)
        # Handle missing columns if necessary, though provided files should be consistent
        if 'elapsed_seconds' not in df.columns:
            df['elapsed_seconds'] = np.arange(len(df)) * 5 # assume 5s interval
        
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Add demand data
        num_slots = len(df)
        extended_demand = []
        idx = 0
        while len(extended_demand) < num_slots:
            extended_demand.append(demand_pattern[idx % len(demand_pattern)])
            idx += 1
        df['demand'] = extended_demand[:num_slots]
        df['demand_normalized'] = df['demand'] / max_demand
        
        strategies[name] = df
        print(f"Loaded {name}")
    except Exception as e:
        print(f"Failed to load {name}: {e}")

# --- Calculations ---

def nonlinear_carbon_weight(c):
    if c <= 0: return 0.0
    base = 100.0
    x = c / base
    if x >= 1.0:
        return float(x ** 1.8)
    else:
        return float(x ** 0.4)

def calculate_metrics(df):
    # Linear
    energy_per_slot = (
        df['requests_precision_30'] * ENERGY_P30 +
        df['requests_precision_50'] * ENERGY_P50 +
        df['requests_precision_100'] * ENERGY_P100
    )
    carbon_per_slot = df['carbon_now'] * energy_per_slot
    
    # Non-linear
    weights = df['carbon_now'].apply(nonlinear_carbon_weight)
    nonlinear_carbon_per_slot = carbon_per_slot * weights
    
    total_requests = (df['requests_precision_30'] + df['requests_precision_50'] + df['requests_precision_100']).sum()
    
    # Mean Precision
    weighted_prec = (df['requests_precision_30'] * 0.30 + df['requests_precision_50'] * 0.50 + df['requests_precision_100'] * 1.00).sum()
    mean_precision = weighted_prec / max(total_requests, 1)
    
    # Demand metrics
    prec_per_slot = (
        df['requests_precision_30'] * 0.30 +
        df['requests_precision_50'] * 0.50 +
        df['requests_precision_100'] * 1.00
    ) / (df['requests_precision_30'] + df['requests_precision_50'] + df['requests_precision_100']).replace(0, 1)
    
    demand_weighted_prec = (prec_per_slot * df['demand_normalized']).sum() / df['demand_normalized'].sum()
    
    # Smart Allocation
    high_demand_mask = df['demand_normalized'] >= 0.8
    low_demand_mask = df['demand_normalized'] <= 0.3
    
    hd_prec = prec_per_slot[high_demand_mask].mean() if high_demand_mask.any() else 0
    ld_prec = prec_per_slot[low_demand_mask].mean() if low_demand_mask.any() else 0.01
    smart_ratio = hd_prec / max(ld_prec, 0.01)
    
    return {
        'total_carbon_linear': carbon_per_slot.sum(),
        'total_carbon_nonlinear': nonlinear_carbon_per_slot.sum(),
        'mean_precision': mean_precision,
        'demand_weighted_precision': demand_weighted_prec,
        'smart_allocation_ratio': smart_ratio
    }

results = {}
for name, df in strategies.items():
    results[name] = calculate_metrics(df)

# Calculate Reductions (relative to p100)
baseline = results['p100']
comparison_rows = []
for name, m in results.items():
    red_linear = (baseline['total_carbon_linear'] - m['total_carbon_linear']) / baseline['total_carbon_linear'] * 100
    red_nonlinear = (baseline['total_carbon_nonlinear'] - m['total_carbon_nonlinear']) / baseline['total_carbon_nonlinear'] * 100
    
    precision_loss = 1.0 - m['mean_precision']
    eff_nonlin = red_nonlinear / (precision_loss * 100) if precision_loss > 0.001 else 0
    
    comparison_rows.append({
        'Strategy': name,
        'Carbon Reduction Linear (%)': red_linear,
        'Carbon Reduction NonLinear (%)': red_nonlinear,
        'Mean Precision': m['mean_precision'],
        'Carbon Efficiency NonLinear': eff_nonlin,
        'Demand-Weighted Precision': m['demand_weighted_precision'],
        'Smart Allocation Ratio': m['smart_allocation_ratio']
    })

competing = pd.DataFrame(comparison_rows)
competing = competing[competing['Strategy'] != 'p100'] 

# --- Plotting ---

# 1. Carbon Reduction Comparison
fig, ax = plt.subplots(figsize=(10, 6))
x = np.arange(len(competing))
width = 0.35
sorted_df = competing.sort_values('Carbon Reduction Linear (%)', ascending=False)
ax.bar(x - width/2, sorted_df['Carbon Reduction Linear (%)'], width, label='Linear', color='steelblue', edgecolor='black')
ax.bar(x + width/2, sorted_df['Carbon Reduction NonLinear (%)'], width, label='Non-Linear', color='coral', edgecolor='black')
ax.set_ylabel('Carbon Reduction (%)')
ax.set_title('Linear vs Non-Linear Carbon Reduction')
ax.set_xticks(x)
ax.set_xticklabels(sorted_df['Strategy'], rotation=45, ha='right')
ax.legend()
plt.tight_layout()
plt.savefig(f'{OUTPUT_DIR}/carbon_reduction_comparison.png')
plt.close()

# 2. Carbon Efficiency Comparison
fig, ax = plt.subplots(figsize=(10, 6))
sorted_df = competing.sort_values('Carbon Efficiency NonLinear', ascending=True)
colors = ['darkgreen' if s == 'forecast-aware-global' else 'steelblue' for s in sorted_df['Strategy']]
ax.barh(sorted_df['Strategy'], sorted_df['Carbon Efficiency NonLinear'], color=colors, edgecolor='black')
ax.set_xlabel('Non-Linear Carbon Efficiency Score')
ax.set_title('Carbon Efficiency Comparison')
plt.tight_layout()
plt.savefig(f'{OUTPUT_DIR}/carbon_efficiency_comparison.png')
plt.close()

# 3. Smart Allocation Ratio
fig, ax = plt.subplots(figsize=(8, 6))
sorted_df = competing.sort_values('Smart Allocation Ratio', ascending=True)
colors = ['darkgreen' if s == 'forecast-aware-global' else 'coral' for s in sorted_df['Strategy']]
ax.barh(sorted_df['Strategy'], sorted_df['Smart Allocation Ratio'], color=colors, edgecolor='black')
ax.axvline(x=1.0, color='red', linestyle='--', label='Naive (1.0x)')
ax.set_xlabel('Smart Allocation Ratio (High/Low Demand Precision)')
ax.set_title('Smart Allocation Ratio')
ax.legend()
plt.tight_layout()
plt.savefig(f'{OUTPUT_DIR}/smart_allocation_ratio.png')
plt.close()

# 4. Radar Chart
advanced_strategies = ['forecast-aware', 'forecast-aware-global', 'credit-greedy']
advanced_df = competing[competing['Strategy'].isin(advanced_strategies)].copy()
metrics_to_plot = ['Carbon Reduction Linear (%)', 'Carbon Efficiency NonLinear', 'Demand-Weighted Precision', 'Smart Allocation Ratio']
# Normalize
normalized_df = advanced_df.copy()
for metric in metrics_to_plot:
    min_val = competing[metric].min()
    max_val = competing[metric].max()
    if max_val > min_val:
        normalized_df[metric] = (normalized_df[metric] - min_val) / (max_val - min_val)
    else:
        normalized_df[metric] = 0.5

categories = [m.replace(' (%)', '').replace('Carbon Efficiency NonLinear', 'Efficiency') for m in metrics_to_plot]
N = len(categories)
angles = [n / float(N) * 2 * pi for n in range(N)]
angles += angles[:1]

fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(projection='polar'))
colors_map = {'forecast-aware-global': 'darkgreen', 'forecast-aware': 'steelblue', 'credit-greedy': 'coral'}

for idx, row in normalized_df.iterrows():
    strategy = row['Strategy']
    values = row[metrics_to_plot].values.flatten().tolist()
    values += values[:1]
    ax.plot(angles, values, 'o-', linewidth=2, label=strategy, color=colors_map.get(strategy, 'gray'))
    ax.fill(angles, values, alpha=0.15, color=colors_map.get(strategy, 'gray'))

ax.set_xticks(angles[:-1])
ax.set_xticklabels(categories)
ax.set_title('Strategy Comparison Radar')
ax.legend(loc='upper right', bbox_to_anchor=(1.3, 1.1))
plt.tight_layout()
plt.savefig(f'{OUTPUT_DIR}/radar_chart.png')
plt.close()

# 5. Forecast Aware Results
df_fa = strategies['forecast-aware']
fig, ax1 = plt.subplots(figsize=(12, 6))
ax2 = ax1.twinx()
ax1.plot(df_fa['elapsed_seconds'], df_fa['carbon_now'], 'k--', label='Carbon Intensity', alpha=0.5)
ax2.stackplot(df_fa['elapsed_seconds'], 
              df_fa['requests_precision_30'], 
              df_fa['requests_precision_50'], 
              df_fa['requests_precision_100'],
              labels=['P30', 'P50', 'P100'], alpha=0.6)
ax1.set_ylabel('Carbon Intensity')
ax2.set_ylabel('Requests')
ax1.set_title('Forecast-Aware Strategy: Execution Profile')
lines, labels = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines + lines2, labels + labels2, loc='upper left')
plt.tight_layout()
plt.savefig(f'{OUTPUT_DIR}/forecast_aware_results.png')
plt.close()

# 6. Credit Greedy Results
df_cg = strategies['credit-greedy']
fig, ax1 = plt.subplots(figsize=(12, 6))
ax2 = ax1.twinx()
ax1.plot(df_cg['elapsed_seconds'], df_cg['carbon_now'], 'k--', label='Carbon Intensity', alpha=0.5)
ax2.stackplot(df_cg['elapsed_seconds'], 
              df_cg['requests_precision_30'], 
              df_cg['requests_precision_50'], 
              df_cg['requests_precision_100'],
              labels=['P30', 'P50', 'P100'], alpha=0.6)
ax1.set_ylabel('Carbon Intensity')
ax2.set_ylabel('Requests')
ax1.set_title('Credit-Greedy Strategy: Execution Profile')
lines, labels = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines + lines2, labels + labels2, loc='upper left')
plt.tight_layout()
plt.savefig(f'{OUTPUT_DIR}/credit_greedy_results.png')
plt.close()

print("Plots generated successfully.")
