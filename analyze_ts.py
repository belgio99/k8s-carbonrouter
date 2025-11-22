import csv
import sys
import statistics

try:
    filename = '/Users/belgio/git-repos/k8s-carbonaware-scheduler/experiments/results/autoscaling_20251122_184017/forecast-aware-global-with-throttle/timeseries.csv'
    
    carbons = []
    throttles = []
    credits = []
    
    with open(filename, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                c = float(row['carbon_now'])
                t = float(row['throttle_factor'])
                cr = float(row['credit_balance'])
                carbons.append(c)
                throttles.append(t)
                credits.append(cr)
            except (ValueError, KeyError):
                continue

    if not carbons:
        print("No data found")
        sys.exit(1)

    print(f"Count: {len(carbons)}")
    
    print("\nCarbon Now:")
    print(f"Min: {min(carbons)}")
    print(f"Max: {max(carbons)}")
    print(f"Mean: {statistics.mean(carbons)}")
    
    print("\nThrottle Factor:")
    print(f"Min: {min(throttles)}")
    print(f"Max: {max(throttles)}")
    print(f"Mean: {statistics.mean(throttles)}")
    
    print("\nCredit Balance:")
    print(f"Min: {min(credits)}")
    print(f"Max: {max(credits)}")
    print(f"Mean: {statistics.mean(credits)}")

    # Check correlation manually (simple direction)
    print("\nFirst 5 rows:")
    with open(filename, 'r') as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i < 5:
                print(f"T={row['timestamp']}, C={row['carbon_now']}, Th={row['throttle_factor']}, Cr={row['credit_balance']}")
            else:
                break

except Exception as e:
    print(e)
