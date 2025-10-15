# Operator

The operator provisions and maintains the Kubernetes resources required for
carbon-aware routing. It reconciles `TrafficSchedule` custom resources and
services that opt in to carbonrouter via the `carbonrouter/enabled` label.

## Controllers

### TrafficScheduleReconciler

- Watches `scheduling.carbonrouter.io/v1alpha1` `TrafficSchedule` resources.
- Discovers carbon strategy deployments in the same namespace by reading the
  `carbonstat.precision` label on `Deployment` objects.
- Pushes the discovered strategies and scheduler configuration to the decision
  engine using `PUT /config/<namespace>/<name>`.
- Retrieves the generated schedule from `GET /schedule/<namespace>/<name>` and
  updates `status` with flavour weights, credit metrics, forecast data, and the
  `validUntil` timestamp.
- Requeues the reconcile loop as the schedule approaches expiry.

### FlavourRouterReconciler

- Watches `Service` resources labelled with `carbonrouter/enabled=true`.
- Ensures the buffer service Deployments (`router`, `consumer`) and Services are
  created in the target namespace with the correct environment variables.
- Creates KEDA `ScaledObject` resources per flavour to autoscale the target
  deployments based on queue depth and metrics.
- Generates Istio `DestinationRule` and `VirtualService` objects that map
  incoming traffic to precision-based subsets.
- Handles cleanup when the enabling label is removed from a service.

## Build & Deploy

Prerequisites: Go 1.23+, Docker, kubectl, and access to a Kubernetes cluster.

```bash
cd operator
make install          # install CRDs only
make run              # run locally against the current kubeconfig (no RBAC)

make docker-build IMG=<registry>/carbonrouter-operator:dev
make docker-push IMG=<registry>/carbonrouter-operator:dev
make deploy IMG=<registry>/carbonrouter-operator:dev
```

Remove the operator with `make undeploy` and clean up CRDs via `make uninstall`.

## Configuration

Key environment variables for the controller manager (see `config/manager`):

| Name | Default | Description |
| ---- | ------- | ----------- |
| `ENABLE_LEADER_ELECTION` | `false` | Enables controller-runtime leader election when running multiple replicas. |
| `METRICS_BIND_ADDRESS` | `0` | Address for metrics server (`:8443` for HTTPS). |
| `HEALTH_PROBE_BIND_ADDRESS` | `:8081` | Address for readiness/liveness probes. |
| `METRICS_SECURE` | `true` | Serve metrics over HTTPS when `true`. |
| `WEBHOOK_CERT_PATH` | unset | Optional path to webhook TLS certificates. |

High-level defaults for buffer service deployments are templated in
`internal/controller/flavourrouter_controller.go`. Override them with CRD spec
fields such as `spec.router.resources`, `spec.consumer.autoscaling`, and
`spec.target.autoscaling`.

## Development Notes

- Generated binaries (`controller-gen`, `kustomize`) are vendored under `bin/`.
- CRDs reside in `config/crd/bases/`; run `make manifests` after changing API
  types.
- Unit tests can be run with `make test`. To execute envtest-based suites,
  ensure the Kubernetes test binaries are downloaded (`make envtest`).

## Related Resources

- CRD definitions: `operator/config/crd/bases/`
- Sample manifests: `operator/config/samples/`
- Umbrella charts: `helm/carbonshift-umbrella`

