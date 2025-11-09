# Tomorrow's Startup Checklist

Use this checklist when you restart the cluster tomorrow.

## Pre-Flight (5 min)

- [ ] Cluster is up and running
- [ ] Verify pods are running:
  ```bash
  kubectl get pods -n carbonstat | grep -E "router|consumer|precision"
  kubectl get pods -n carbonrouter-system | grep decision-engine
  ```
- [ ] Mock carbon API responding:
  ```bash
  curl -s http://localhost:5001/scenario | jq '.scenario'
  ```

## Automated Startup (2-3 min)

- [ ] Run automated startup:
  ```bash
  cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/experiments
  ./startup.sh
  ```
- [ ] All checks pass (green ✅ checkmarks)
- [ ] Carbon forecast metrics show values
- [ ] End-to-end test returns valid strategy

## If Automated Startup Fails

- [ ] Check decision engine HTTP connectivity:
  ```bash
  curl -s http://localhost:18004/healthz
  ```
- [ ] Manually initialize:
  ```bash
  ./init_decision_engine.sh
  ```
- [ ] Verify carbon metrics exist:
  ```bash
  curl -s http://localhost:18003/metrics | grep 'scheduler_forecast_intensity{horizon="now"'
  ```

## Ready to Benchmark (1 min)

- [ ] Test a single request works:
  ```bash
  curl -s -X POST http://localhost:18000/avg \
    -H "Content-Type: application/json" \
    -d '{"numbers":[1,2,3,4,5]}'
  ```
- [ ] Response includes `"strategy"` field
- [ ] No errors in response

## Run Benchmarks (10-15 min per test)

- [ ] Start credit-greedy test:
  ```bash
  cd experiments
  python3 run_simple_benchmark.py --policy credit-greedy
  ```
- [ ] Monitor progress (look for `/metrics` output every 30s)
- [ ] Test completes in ~12 minutes
- [ ] Results in: `experiments/results/simple_TIMESTAMP/credit-greedy/`

## Verify Results (2 min)

- [ ] Check CSV has data:
  ```bash
  ls -la experiments/results/simple_*/credit-greedy/
  tail -5 experiments/results/simple_*/credit-greedy/timeseries.csv
  ```
- [ ] CSV has columns: `carbon_now, carbon_next` with values (not empty!)
- [ ] Locust CSV shows request counts per period
- [ ] No obvious errors in logs

## Optional: Run Additional Tests

- [ ] forecast-aware test:
  ```bash
  python3 run_simple_benchmark.py --policy forecast-aware
  ```
- [ ] forecast-aware-global test:
  ```bash
  python3 run_simple_benchmark.py --policy forecast-aware-global
  ```

## If Something Goes Wrong

### Issue: Port-forwards not working
```bash
lsof -ti:18000-18004 | xargs kill -9
./startup.sh  # Re-run to establish new port-forwards
```

### Issue: Decision engine returning 404
```bash
./init_decision_engine.sh
sleep 5
curl -s http://localhost:18003/metrics | grep scheduler_forecast_intensity
```

### Issue: Carbon metrics still empty after initialization
```bash
# Check if router needs restart to pick up new schedule
kubectl delete pod -n carbonstat -l app.kubernetes.io/name=buffer-service-router
sleep 10
./startup.sh  # Re-verify everything
```

### Issue: Requests failing with 503
```bash
# Router doesn't have schedule yet
kubectl logs -n carbonstat -l app.kubernetes.io/name=buffer-service-router | grep "Router ready"

# If not printed, wait longer or restart:
kubectl delete pod -n carbonstat -l app.kubernetes.io/name=buffer-service-router
```

---

**Estimated total time: 15-20 minutes** to go from cold cluster restart to completed first benchmark ✨
