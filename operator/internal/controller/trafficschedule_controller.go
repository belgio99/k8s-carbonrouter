/*
Copyright 2025 belgio99.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package controller

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/builder"
	"sigs.k8s.io/controller-runtime/pkg/event"
	"sigs.k8s.io/controller-runtime/pkg/predicate"

	schedulingv1alpha1 "github.com/belgio99/k8s-carbonrouter/operator/api/v1alpha1"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// TrafficScheduleReconciler reconciles a TrafficSchedule object
type TrafficScheduleReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

const (
	pollInterval            = 1 * time.Minute
	engineBaseURL           = "http://carbonrouter-decision-engine.carbonrouter-system.svc.cluster.local"
	configHashAnnotation    = "scheduling.carbonrouter.io/config-hash"
	schedulePendingInterval = 5 * time.Second
)

var httpClient = &http.Client{Timeout: 5 * time.Second}

const (
	strategyNameLabel    = "carbonstat.strategy"
	carbonIntensityLabel = "carbonstat.emissions"
)

type schedulerFlavour struct {
	Name            string            `json:"name"`
	Precision       float64           `json:"precision"`
	CarbonIntensity float64           `json:"carbonIntensity"`
	Enabled         bool              `json:"enabled"`
	Annotations     map[string]string `json:"annotations,omitempty"`
}

// +kubebuilder:rbac:groups=scheduling.carbonrouter.io,resources=trafficschedules,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=scheduling.carbonrouter.io,resources=trafficschedules/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=scheduling.carbonrouter.io,resources=trafficschedules/finalizers,verbs=update

func (r *TrafficScheduleReconciler) discoverFlavours(ctx context.Context, namespace string) ([]schedulerFlavour, error) {
	logger := ctrl.LoggerFrom(ctx).WithName("[TrafficSchedule][Discovery]")

	var deployments appsv1.DeploymentList
	// Search cluster-wide for deployments with precision labels, not just in the TrafficSchedule namespace
	if err := r.List(ctx, &deployments); err != nil {
		return nil, err
	}

	flavours := make([]schedulerFlavour, 0)
	seen := make(map[string]struct{})

	for _, dep := range deployments.Items {
		labels := dep.GetLabels()
		precisionValue := labels[precisionLabel]
		if precisionValue == "" {
			continue
		}

		precision, err := strconv.ParseFloat(precisionValue, 64)
		if err != nil {
			logger.Info("Skipping deployment with invalid precision label", "deployment", dep.Name, "value", precisionValue)
			continue
		}
		if precision > 1 {
			precision = precision / 100
		}
		if precision < 0 {
			precision = 0
		}
		if precision > 1 {
			precision = 1
		}

		precisionName := fmt.Sprintf("precision-%d", int(math.Round(precision*100)))

		if _, exists := seen[precisionName]; exists {
			logger.Info("Duplicate precision detected, keeping first occurrence", "precision", precisionName, "deployment", dep.Name)
			continue
		}

		carbonIntensity := 0.0
		if carbonLabel := labels[carbonIntensityLabel]; carbonLabel != "" {
			if value, err := strconv.ParseFloat(carbonLabel, 64); err == nil {
				carbonIntensity = value
			} else {
				logger.Info("Ignoring invalid carbon intensity label", "deployment", dep.Name, "value", carbonLabel)
			}
		}

		annotations := make(map[string]string, len(labels))
		for key, value := range labels {
			annotations[key] = value
		}

		flavours = append(flavours, schedulerFlavour{
			Name:            precisionName,
			Precision:       precision,
			CarbonIntensity: carbonIntensity,
			Enabled:         true,
			Annotations:     annotations,
		})
		seen[precisionName] = struct{}{}
	}

	sort.Slice(flavours, func(i, j int) bool {
		return strategies[i].Precision > strategies[j].Precision
	})

	return flavours, nil
}

// Reconcile is part of the main kubernetes reconciliation loop which aims to
// move the current state of the cluster closer to the desired state.
// TODO(user): Modify the Reconcile function to compare the state specified by
// the TrafficSchedule object against the actual cluster state, and then
// perform operations to make the cluster state reflect the state specified by
// the user.
//
// For more details, check Reconcile and its Result here:
// - https://pkg.go.dev/sigs.k8s.io/controller-runtime@v0.20.4/pkg/reconcile
func (r *TrafficScheduleReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := ctrl.LoggerFrom(ctx).WithName("[TrafficSchedule]")
	log.Info("Reconciling TrafficSchedule", "name", req.Name)

	var existing schedulingv1alpha1.TrafficSchedule
	if err := r.Get(ctx, req.NamespacedName, &existing); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	flavours, err := r.discoverFlavours(ctx, req.Namespace)
	if err != nil {
		log.Error(err, "Failed to discover strategy deployments")
		return ctrl.Result{}, err
	}
	if len(flavours) == 0 {
		log.Info("No carbon flavours discovered – scheduler will use defaults")
	}

	payload := buildSchedulerConfigPayload(existing.Spec, strategies)
	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		log.Error(err, "Failed to serialise scheduler payload")
		return ctrl.Result{}, err
	}

	prevHash := ""
	if existing.Annotations != nil {
		prevHash = existing.Annotations[configHashAnnotation]
	}
	configHash := fmt.Sprintf("%x", sha256.Sum256(payloadBytes))
	if prevHash != configHash {
		if err := pushSchedulerConfig(req.Namespace, req.Name, payload); err != nil {
			log.Error(err, "Failed to push scheduler configuration")
			return ctrl.Result{}, err
		}

		original := existing.DeepCopy()
		updated := existing.DeepCopy()
		if updated.Annotations == nil {
			updated.Annotations = map[string]string{}
		}
		updated.Annotations[configHashAnnotation] = configHash
		if err := r.Patch(ctx, updated, client.MergeFrom(original)); err != nil {
			log.Error(err, "Failed to persist scheduler config hash")
			return ctrl.Result{}, err
		}
		if err := r.Get(ctx, req.NamespacedName, &existing); err != nil {
			return ctrl.Result{}, err
		}
	} else {
		log.V(1).Info("Scheduler configuration unchanged; skipping push")
	}

	// 1) Get schedule from decision engine
	url := fmt.Sprintf("%s/schedule/%s/%s", engineBaseURL, req.Namespace, req.Name)
	resp, err := httpClient.Get(url)
	if err != nil {
		log.Error(err, "Failed to get traffic schedule")
		return ctrl.Result{}, err
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusAccepted || resp.StatusCode == http.StatusNoContent {
		log.Info("Decision engine reports schedule pending", "statusCode", resp.StatusCode)
		return ctrl.Result{RequeueAfter: schedulePendingInterval}, nil
	}
	if resp.StatusCode >= http.StatusBadRequest {
		err := fmt.Errorf("unexpected status code: %s", resp.Status)
		log.Error(err, "Failed to get traffic schedule")
		return ctrl.Result{}, err
	}

	// 2) Temp struct to decode the response
	var remote struct {
		Strategies []struct {
			Name            string  `json:"name"`
			Precision       int     `json:"precision"`
			Weight          int     `json:"weight"`
			CarbonIntensity float64 `json:"carbonIntensity"`
		} `json:"flavours"`
		Policy struct {
			Name string `json:"name"`
		} `json:"policy"`
		ValidUntilISO string `json:"validUntil"`
		Credits       struct {
			Balance   float64 `json:"balance"`
			Velocity  float64 `json:"velocity"`
			Target    float64 `json:"target"`
			Min       float64 `json:"min"`
			Max       float64 `json:"max"`
			Allowance float64 `json:"allowance"`
		} `json:"credits"`
		Processing struct {
			Throttle float64          `json:"throttle"`
			Ceilings map[string]int32 `json:"ceilings"`
		} `json:"processing"`
		Diagnostics map[string]float64 `json:"diagnostics"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&remote); err != nil {
		log.Error(err, "Failed to decode traffic schedule response")
		return ctrl.Result{}, err
	}
	if remote.ValidUntilISO == "" || len(remote.Flavours) == 0 {
		log.Info("Decision engine returned incomplete schedule", "flavours", len(remote.Flavours), "validUntil", remote.ValidUntilISO)
		return ctrl.Result{RequeueAfter: schedulePendingInterval}, nil
	}

	// 3) Create the status for the TrafficSchedule CR
	var diagnostics map[string]string
	if len(remote.Diagnostics) > 0 {
		diagnostics = make(map[string]string, len(remote.Diagnostics))
		for key, value := range remote.Diagnostics {
			diagnostics[key] = formatFloat(value)
		}
	}

	status := schedulingv1alpha1.TrafficScheduleStatus{
		ActivePolicy:   remote.Policy.Name,
		CreditBalance:  formatFloat(remote.Credits.Balance),
		CreditVelocity: formatFloat(remote.Credits.Velocity),
		CreditTarget:   formatFloat(remote.Credits.Target),
		CreditMin:      formatFloat(remote.Credits.Min),
		CreditMax:      formatFloat(remote.Credits.Max),
		Diagnostics:    diagnostics,
	}
	if remote.Processing.Throttle > 0 {
		status.ProcessingThrottle = formatFloat(remote.Processing.Throttle)
	}
	if len(remote.Processing.Ceilings) > 0 {
		status.EffectiveReplicaCeilings = remote.Processing.Ceilings
	}
	for _, flavour := range remote.Flavours {
		status.Flavours = append(status.Flavours, schedulingv1alpha1.StrategyDecision{
			Precision: strategy.Precision,
			Weight:    strategy.Weight,
		})
	}
	if t, err := time.Parse(time.RFC3339, remote.ValidUntilISO); err == nil {
		status.ValidUntil = metav1.NewTime(t)
	}

	sort.Slice(status.Flavours, func(i, j int) bool {
		return status.Flavours[i].Precision < status.Flavours[j].Precision
	})

	// 4) Overwrite old status with the new one
	statusChanged := !reflect.DeepEqual(existing.Status, status)
	if statusChanged {
		existing.Status = status
		if err := r.Status().Update(ctx, &existing); err != nil {
			log.Error(err, "unable to update TrafficSchedule status")
			return ctrl.Result{}, err
		}
	}
	next := pollInterval
	if !status.ValidUntil.IsZero() {
		until := time.Until(status.ValidUntil.Time)
		if until <= 0 {
			until = 1 * time.Second
		}
		if until < next {
			next = until
		}
	}

	log.Info("TrafficSchedule reconcile complete",
		"nextReconcileIn", next)

	return ctrl.Result{RequeueAfter: next}, nil
}

// SetupWithManager sets up the controller with the Manager.
func (r *TrafficScheduleReconciler) SetupWithManager(mgr ctrl.Manager) error {
	// filter: only reconcile on create or spec update
	p := predicate.Funcs{
		CreateFunc: func(e event.CreateEvent) bool { return true },
		UpdateFunc: func(e event.UpdateEvent) bool {
			oldTS := e.ObjectOld.(*schedulingv1alpha1.TrafficSchedule)
			newTS := e.ObjectNew.(*schedulingv1alpha1.TrafficSchedule)
			return !reflect.DeepEqual(oldTS.Spec, newTS.Spec)
		},
	}

	return ctrl.NewControllerManagedBy(mgr).
		For(&schedulingv1alpha1.TrafficSchedule{}, builder.WithPredicates(p)).
		Complete(r)
}

func pushSchedulerConfig(namespace, name string, payload map[string]interface{}) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}

	url := fmt.Sprintf("%s/config/%s/%s", engineBaseURL, namespace, name)
	req, err := http.NewRequest(http.MethodPut, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= http.StatusBadRequest {
		return fmt.Errorf("scheduler config rejected: %s", resp.Status)
	}

	return nil
}

func buildSchedulerConfigPayload(spec schedulingv1alpha1.TrafficScheduleSpec, strategies []schedulerFlavour) map[string]interface{} {
	cfg := map[string]interface{}{}
	s := spec.Scheduler

	assignFloat(cfg, "targetError", s.TargetError)
	assignFloat(cfg, "creditMin", s.CreditMin)
	assignFloat(cfg, "creditMax", s.CreditMax)
	if s.CreditWindow != nil {
		cfg["creditWindow"] = *s.CreditWindow
	}
	if s.Policy != nil {
		cfg["policy"] = *s.Policy
	}
	if s.ValidFor != nil {
		cfg["validFor"] = *s.ValidFor
	}
	if s.DiscoveryInterval != nil {
		cfg["discoveryInterval"] = *s.DiscoveryInterval
	}
	if s.CarbonTarget != nil && *s.CarbonTarget != "" {
		cfg["carbonTarget"] = *s.CarbonTarget
	}
	if s.CarbonTimeout != nil {
		cfg["carbonTimeout"] = *s.CarbonTimeout
	}
	if s.CarbonCacheTTL != nil {
		cfg["carbonCacheTTL"] = *s.CarbonCacheTTL
	}

	components := map[string]map[string]int32{}
	if bounds := replicaBounds(spec.Router); bounds != nil {
		components["router"] = bounds
	}
	if bounds := replicaBounds(spec.Consumer); bounds != nil {
		components["consumer"] = bounds
	}
	if bounds := targetReplicaBounds(spec.Target); bounds != nil {
		components["target"] = bounds
	}
	if len(components) > 0 {
		cfg["components"] = components
	}

	if len(flavours) > 0 {
		cfg["flavours"] = strategies
	}

	return cfg
}

func formatFloat(value float64) string {
	return strconv.FormatFloat(value, 'f', -1, 64)
}

func assignFloat(target map[string]interface{}, key string, value *string) {
	if value == nil {
		return
	}
	trimmed := strings.TrimSpace(*value)
	if trimmed == "" {
		return
	}
	if parsed, err := strconv.ParseFloat(trimmed, 64); err == nil {
		target[key] = parsed
	}
}

func replicaBounds(component schedulingv1alpha1.ComponentConfig) map[string]int32 {
	autoscaling := component.Autoscaling
	bounds := map[string]int32{}
	if autoscaling.MinReplicaCount != nil {
		bounds["minReplicas"] = *autoscaling.MinReplicaCount
	}
	if autoscaling.MaxReplicaCount != nil {
		bounds["maxReplicas"] = *autoscaling.MaxReplicaCount
	}
	if len(bounds) == 0 {
		return nil
	}
	return bounds
}

func targetReplicaBounds(target schedulingv1alpha1.TargetConfig) map[string]int32 {
	autoscaling := target.Autoscaling
	bounds := map[string]int32{}
	if autoscaling.MinReplicaCount != nil {
		bounds["minReplicas"] = *autoscaling.MinReplicaCount
	}
	if autoscaling.MaxReplicaCount != nil {
		bounds["maxReplicas"] = *autoscaling.MaxReplicaCount
	}
	if len(bounds) == 0 {
		return nil
	}
	return bounds
}
