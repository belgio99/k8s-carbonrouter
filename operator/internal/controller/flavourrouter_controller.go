// controllers/flavourrouter_controller.go
package controller

import (
	"context"
	"fmt"
	"sort"
	"strconv"
	"time"

	//appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/equality"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/client-go/util/retry"
	"k8s.io/utils/ptr"

	kedav1alpha1 "github.com/kedacore/keda/v2/apis/keda/v1alpha1"
	networkingapi "istio.io/api/networking/v1alpha3"
	networkingkube "istio.io/client-go/pkg/apis/networking/v1alpha3"
	appsv1 "k8s.io/api/apps/v1"
	rbacv1 "k8s.io/api/rbac/v1"

	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/builder"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/event"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/predicate"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	schedulingv1alpha1 "github.com/belgio99/k8s-carbonrouter/operator/api/v1alpha1"
)

/* ─────────────────────────────────────────  Constants  ───────────────────────────────────────── */
const (
	precisionLabel         = "carbonstat.precision"
	parentServiceLabel     = "carbonrouter/parent-service"
	enableLabel            = "carbonrouter/enabled"
	origReplicasAnnotation = "carbonrouter/original-replicas"
	defaultRequeue         = 30 * time.Second
)

func collectPrecisions(strategies []schedulingv1alpha1.StrategyDecision) []int {
	uniq := make(map[int]struct{})
	for _, strategy := range strategies {
		if strategy.Precision > 0 {
			uniq[strategy.Precision] = struct{}{}
		}
	}
	if len(uniq) == 0 {
		return nil
	}
	values := make([]int, 0, len(uniq))
	for value := range uniq {
		values = append(values, value)
	}
	sort.Ints(values)
	return values
}

func precisionSubsetName(precision int) string {
	return fmt.Sprintf("precision-%d", precision)
}

func precisionHeaderValue(precision int) string {
	return fmt.Sprintf("%d", precision)
}

func precisionQueueSuffix(precision int) string {
	return precisionSubsetName(precision)
}

func directQueueName(namespace, service string, precision int) string {
	return fmt.Sprintf("%s.%s.direct.%s", namespace, service, precisionQueueSuffix(precision))
}

func bufferedQueueName(namespace, service string, precision int) string {
	return fmt.Sprintf("%s.%s.queue.%s", namespace, service, precisionQueueSuffix(precision))
}

func buildSubsets(precisions []int) []*networkingapi.Subset {
	subsets := make([]*networkingapi.Subset, 0, len(precisions))
	for _, precision := range precisions {
		subsets = append(subsets, &networkingapi.Subset{
			Name:   precisionSubsetName(precision),
			Labels: map[string]string{precisionLabel: precisionHeaderValue(precision)},
		})
	}
	return subsets
}

func (r *FlavourRouterReconciler) discoverStrategyDeployments(ctx context.Context, svc *corev1.Service) (map[int]string, error) {
	var deployments appsv1.DeploymentList
	if err := r.List(ctx, &deployments, client.InNamespace(svc.Namespace), client.MatchingLabels{parentServiceLabel: svc.Name}); err != nil {
		return nil, err
	}
	result := make(map[int]string)
	for _, dep := range deployments.Items {
		labelValue := dep.Labels[precisionLabel]
		if labelValue == "" {
			continue
		}
		precision, err := strconv.Atoi(labelValue)
		if err != nil {
			ctrl.LoggerFrom(ctx).WithName("[FlavourRouter]").Info("Skipping deployment with invalid precision label", "deployment", dep.Name, "value", labelValue)
			continue
		}
		if _, exists := result[precision]; exists {
			ctrl.LoggerFrom(ctx).WithName("[FlavourRouter]").Info("Multiple deployments found for precision, keeping first", "precision", precision, "existing", result[precision], "ignored", dep.Name)
			continue
		}
		result[precision] = dep.Name
	}
	return result, nil
}

func (r *FlavourRouterReconciler) precisionScaledObjectNames(ctx context.Context, svc *corev1.Service) []string {
	var soList kedav1alpha1.ScaledObjectList
	if err := r.List(ctx, &soList, client.InNamespace(svc.Namespace), client.MatchingLabels{parentServiceLabel: svc.Name}); err != nil {
		ctrl.LoggerFrom(ctx).WithName("[FlavourRouter]").Error(err, "Failed to list scaled objects for cleanup")
		return nil
	}
	names := make([]string, 0)
	for _, so := range soList.Items {
		names = append(names, so.Name)
	}
	sort.Strings(names)
	return names
}

/* ─────────────────────────────────────── Reconciler  ────────────────────────────────────────── */

type FlavourRouterReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

/* -------------------------- RBAC -------------------------- */

// +kubebuilder:rbac:groups=core,resources=services;serviceaccounts,verbs=get;list;watch;create;update;patch
// +kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;list;watch;create;update;patch
// +kubebuilder:rbac:groups=scheduling.carbonrouter.io,resources=trafficschedules,verbs=get;list;watch
// +kubebuilder:rbac:groups=networking.istio.io,resources=virtualservices;destinationrules,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=keda.sh,resources=scaledobjects,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=clusterrolebindings,verbs=get;list;watch;create;update;patch

/* -------------------------- Reconcile -------------------------- */

func (r *FlavourRouterReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := ctrl.LoggerFrom(ctx).WithName("[FlavourRouter]").WithValues("service", req.NamespacedName)

	// 1. Service opt-in
	// Gets the service that has the label "carbonrouter/enabled=true", which is our "target" service.
	var svc corev1.Service
	if err := r.Get(ctx, req.NamespacedName, &svc); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}
	if svc.Labels[enableLabel] != "true" {
		log.Info("Service no longer has carbonrouter enable label, cleaning up resources")
		return ctrl.Result{}, r.cleanupResources(ctx, &svc)
	}

	// 2. Get the TrafficSchedule CR from the cluster
	// Look for TrafficSchedules cluster-wide (not just in the service namespace)
	var tsList schedulingv1alpha1.TrafficScheduleList
	if err := r.List(ctx, &tsList); err != nil {
		return ctrl.Result{}, err
	}
	if len(tsList.Items) == 0 {
		log.Info("No TrafficSchedule – requeue") // if no TrafficSchedule is found, requeue
		return ctrl.Result{RequeueAfter: defaultRequeue}, nil
	}
	ts := tsList.Items[0]
	tsSpec := ts.Spec
	trafficschedule := ts.Status
	precisionList := collectPrecisions(trafficschedule.Flavours)

	deploymentsByPrecision, err := r.discoverStrategyDeployments(ctx, &svc)
	if err != nil {
		log.Error(err, "Failed to discover strategy deployments")
		return ctrl.Result{}, err
	}

	activePrecisions := make([]int, 0, len(precisionList))
	for _, precision := range precisionList {
		if _, ok := deploymentsByPrecision[precision]; ok {
			activePrecisions = append(activePrecisions, precision)
		} else {
			log.Info("Skipping precision without backing deployment", "precision", precision)
		}
	}
	if len(activePrecisions) == 0 {
		log.Info("No precision strategies available with backing deployments – requeue")
		return ctrl.Result{RequeueAfter: defaultRequeue}, nil
	}

	// 4. Create or update all necessary resources
	if err := r.ensureServiceAccount(ctx, &svc); err != nil {
		return ctrl.Result{}, err
	}

	if err := r.ensureClusterRoleBinding(ctx, &svc); err != nil {
		return ctrl.Result{}, err
	}

	if err := r.ensureBufferServiceDeployment(ctx, &svc, "router", tsSpec.Router.Resources, tsSpec.Router.Debug, ts.Namespace); err != nil {
		return ctrl.Result{}, err
	}

	if err := r.ensureBufferServiceService(ctx, &svc, "router"); err != nil {
		return ctrl.Result{}, err
	}

	if err := r.ensureBufferServiceDeployment(ctx, &svc, "consumer", tsSpec.Consumer.Resources, tsSpec.Consumer.Debug, ts.Namespace); err != nil {
		return ctrl.Result{}, err
	}

	if err := r.ensureBufferServiceService(ctx, &svc, "consumer"); err != nil {
		return ctrl.Result{}, err
	}

	// Extract replica ceilings from TrafficSchedule status for carbon-aware autoscaling.
	// The decision engine computes these ceilings based on carbon intensity and quality
	// credits. They are applied to KEDA ScaledObjects to throttle autoscaling during
	// high-carbon periods, trading latency for reduced energy consumption.
	replicaCeilings := trafficschedule.EffectiveReplicaCeilings
	if replicaCeilings == nil {
		replicaCeilings = make(map[string]int32)
	}

	if err := r.ensureRouterScaledObject(ctx, &svc, tsSpec.Router.Autoscaling, replicaCeilings); err != nil {
		return ctrl.Result{}, err
	}

	if err := r.ensureConsumerScaledObject(ctx, &svc, tsSpec.Consumer.Autoscaling, activePrecisions, replicaCeilings); err != nil {
		return ctrl.Result{}, err
	}

	for _, precision := range activePrecisions {
		targetName := deploymentsByPrecision[precision]
		if err := r.ensurePrecisionScaledObject(ctx, &svc, precision, targetName, tsSpec.Target.Autoscaling, replicaCeilings); err != nil {
			return ctrl.Result{}, err
		}
	}

	if err := r.ensureDR(ctx, &svc, activePrecisions); err != nil {
		return ctrl.Result{}, err
	}

	if err := r.ensureVS(ctx, &svc, activePrecisions); err != nil {
		return ctrl.Result{}, err
	}

	// 5. Re-queue based on ValidUntil
	if !trafficschedule.ValidUntil.IsZero() {
		delay := time.Until(trafficschedule.ValidUntil.Time)
		if delay < 0 {
			delay = 0
		}
		log.Info("Requeuing for next TrafficSchedule", "validUntil", trafficschedule.ValidUntil.Time, "delay", delay)
		return ctrl.Result{RequeueAfter: delay}, nil
	}
	return ctrl.Result{}, nil
}

func (r *FlavourRouterReconciler) ensureDR(ctx context.Context, svc *corev1.Service, precisions []int) error {
	log := ctrl.LoggerFrom(ctx).WithName("[FlavourRouter]")
	log.Info("Ensuring DestinationRule for service", "service", svc.Name)
	name := fmt.Sprintf("%s-carbonrouter-dr", svc.Name)
	host := fmt.Sprintf("%s.%s.svc.cluster.local", svc.Name, svc.Namespace)

	newDR := networkingkube.DestinationRule{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: svc.Namespace},
		Spec: networkingapi.DestinationRule{
			Host:    host,
			Subsets: buildSubsets(precisions),
		},
	}
	if err := ctrl.SetControllerReference(svc, &newDR, r.Scheme); err != nil {
		return err
	}

	var currentDR networkingkube.DestinationRule
	err := r.Get(ctx, client.ObjectKey{Namespace: svc.Namespace, Name: name}, &currentDR)
	switch {
	case apierrors.IsNotFound(err):
		return r.Create(ctx, &newDR)
	case err != nil:
		return err
	case !equality.Semantic.DeepEqual(currentDR.Spec, newDR.Spec): // Update the DestinationRule if it differs
		currentDR.Spec = newDR.Spec
		log.Info("DestinationRule was updated", "name", name, "namespace", svc.Namespace)
		return r.Update(ctx, &currentDR)
	}
	return nil
}

func (r *FlavourRouterReconciler) ensureVS(ctx context.Context, svc *corev1.Service, precisions []int) error {
	log := ctrl.LoggerFrom(ctx).WithName("[FlavourRouter]")
	name := fmt.Sprintf("%s-carbonrouter-vs", svc.Name)
	host := fmt.Sprintf("%s.%s.svc.cluster.local", svc.Name, svc.Namespace)
	sourceHost := fmt.Sprintf("%s.%s.svc.cluster.local", svc.Name, svc.Namespace)

	log.Info("Ensuring Flavour VirtualService for service", "service", svc.Name)

	var httpRoutes []*networkingapi.HTTPRoute
	// Traffic forced to go to a specific precision subset
	for _, precision := range precisions {
		subsetName := precisionSubsetName(precision)
		httpRoutes = append(httpRoutes, &networkingapi.HTTPRoute{
			Match: []*networkingapi.HTTPMatchRequest{{
				Headers: map[string]*networkingapi.StringMatch{
					"x-carbonrouter": {MatchType: &networkingapi.StringMatch_Exact{Exact: precisionHeaderValue(precision)}},
				},
			}},
			Route: []*networkingapi.HTTPRouteDestination{{
				Destination: &networkingapi.Destination{Host: host, Subset: subsetName},
				Weight:      100,
			}},
		})
	}

	vs := networkingkube.VirtualService{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: svc.Namespace},
		Spec: networkingapi.VirtualService{
			Hosts: []string{sourceHost},
			Http:  httpRoutes,
		},
	}

	if err := ctrl.SetControllerReference(svc, &vs, r.Scheme); err != nil {
		return err
	}

	var cur networkingkube.VirtualService
	err := r.Get(ctx, client.ObjectKey{Namespace: svc.Namespace, Name: name}, &cur)
	switch {
	case apierrors.IsNotFound(err):
		return r.Create(ctx, &vs)
	case err != nil:
		return err
	case !equality.Semantic.DeepEqual(cur.Spec, vs.Spec):
		cur.Spec = vs.Spec
		log.Info("Flavour VirtualService was updated", "name", name, "namespace", svc.Namespace)
		return r.Update(ctx, &cur)
	}
	return nil
}

func (r *FlavourRouterReconciler) SetupWithManager(mgr ctrl.Manager) error {

	svcPred := predicate.Funcs{
		CreateFunc: func(e event.CreateEvent) bool {
			return e.Object.GetLabels()[enableLabel] == "true"
		},
		UpdateFunc: func(e event.UpdateEvent) bool {
			oldHasLabel := e.ObjectOld.GetLabels()[enableLabel] == "true"
			newHasLabel := e.ObjectNew.GetLabels()[enableLabel] == "true"
			return oldHasLabel || newHasLabel
		},
		DeleteFunc: func(e event.DeleteEvent) bool { return e.Object.GetLabels()[enableLabel] == "true" },
	}

	mapTS := handler.EnqueueRequestsFromMapFunc(func(ctx context.Context, obj client.Object) []reconcile.Request {
		var list corev1.ServiceList
		if err := mgr.GetClient().List(ctx, &list); err != nil {
			return nil
		}
		var out []reconcile.Request
		for _, s := range list.Items {
			if s.Labels[enableLabel] == "true" {
				out = append(out, reconcile.Request{NamespacedName: client.ObjectKeyFromObject(&s)})
			}
		}
		return out
	})

	return ctrl.NewControllerManagedBy(mgr).
		For(&corev1.Service{}, builder.WithPredicates(svcPred)).
		Owns(&appsv1.Deployment{}).
		Owns(&corev1.Service{}).
		Owns(&kedav1alpha1.ScaledObject{}).
		Owns(&corev1.ServiceAccount{}).
		Owns(&rbacv1.ClusterRoleBinding{}).
		Owns(&networkingkube.DestinationRule{}).
		Owns(&networkingkube.VirtualService{}).
		Watches(&schedulingv1alpha1.TrafficSchedule{}, mapTS).
		Complete(r)
}

func (r *FlavourRouterReconciler) cleanupResources(ctx context.Context, svc *corev1.Service) error {
	log := ctrl.LoggerFrom(ctx).WithName("[FlavourRouter][Cleanup]").WithValues("service", svc.Name)
	log.Info("Starting resource cleanup")

	// Delete VirtualService
	vsName := fmt.Sprintf("%s-carbonrouter-vs", svc.Name)
	vs := &networkingkube.VirtualService{ObjectMeta: metav1.ObjectMeta{Name: vsName, Namespace: svc.Namespace}}
	if err := r.Delete(ctx, vs, client.PropagationPolicy(metav1.DeletePropagationBackground)); client.IgnoreNotFound(err) != nil {
		log.Error(err, "Failed to delete VirtualService")
	}

	// Delete DestinationRule
	drName := fmt.Sprintf("%s-carbonrouter-dr", svc.Name)
	dr := &networkingkube.DestinationRule{ObjectMeta: metav1.ObjectMeta{Name: drName, Namespace: svc.Namespace}}
	if err := r.Delete(ctx, dr, client.PropagationPolicy(metav1.DeletePropagationBackground)); client.IgnoreNotFound(err) != nil {
		log.Error(err, "Failed to delete DestinationRule")
	}

	// Delete ScaledObjects (precision-based)
	precisionScaledObjects := r.precisionScaledObjectNames(ctx, svc)
	for _, soName := range precisionScaledObjects {
		so := &kedav1alpha1.ScaledObject{ObjectMeta: metav1.ObjectMeta{Name: soName, Namespace: svc.Namespace}}
		if err := r.Delete(ctx, so, client.PropagationPolicy(metav1.DeletePropagationBackground)); client.IgnoreNotFound(err) != nil {
			log.Error(err, "Failed to delete precision ScaledObject", "ScaledObject", soName)
		}
	}
	consumerSoName := fmt.Sprintf("buffer-service-consumer-%s", svc.Name)
	consumerSo := &kedav1alpha1.ScaledObject{ObjectMeta: metav1.ObjectMeta{Name: consumerSoName, Namespace: svc.Namespace}}
	if err := r.Delete(ctx, consumerSo, client.PropagationPolicy(metav1.DeletePropagationBackground)); client.IgnoreNotFound(err) != nil {
		log.Error(err, "Failed to delete consumer ScaledObject", "ScaledObject", consumerSoName)
	}

	routerSoName := fmt.Sprintf("buffer-service-router-%s", svc.Name)
	routerSo := &kedav1alpha1.ScaledObject{ObjectMeta: metav1.ObjectMeta{Name: routerSoName, Namespace: svc.Namespace}}
	if err := r.Delete(ctx, routerSo, client.PropagationPolicy(metav1.DeletePropagationBackground)); client.IgnoreNotFound(err) != nil {
		log.Error(err, "Failed to delete router ScaledObject", "ScaledObject", routerSoName)
	}

	// Delete Deployments and Services for buffer-service
	for _, component := range []string{"router", "consumer"} {
		depName := fmt.Sprintf("buffer-service-%s-%s", component, svc.Name)
		dep := &appsv1.Deployment{ObjectMeta: metav1.ObjectMeta{Name: depName, Namespace: svc.Namespace}}
		if err := r.Delete(ctx, dep, client.PropagationPolicy(metav1.DeletePropagationBackground)); client.IgnoreNotFound(err) != nil {
			log.Error(err, "Failed to delete Deployment", "Deployment", depName)
		}

		serviceName := fmt.Sprintf("buffer-service-%s-%s", component, svc.Name)
		bufferSvc := &corev1.Service{ObjectMeta: metav1.ObjectMeta{Name: serviceName, Namespace: svc.Namespace}}
		if err := r.Delete(ctx, bufferSvc, client.PropagationPolicy(metav1.DeletePropagationBackground)); client.IgnoreNotFound(err) != nil {
			log.Error(err, "Failed to delete Service", "Service", serviceName)
		}
	}

	// Delete ServiceAccount and ClusterRoleBinding
	saName := fmt.Sprintf("%s-trafficschedule-viewer", svc.Name)
	sa := &corev1.ServiceAccount{ObjectMeta: metav1.ObjectMeta{Name: saName, Namespace: svc.Namespace}}
	if err := r.Delete(ctx, sa, client.PropagationPolicy(metav1.DeletePropagationBackground)); client.IgnoreNotFound(err) != nil {
		log.Error(err, "Failed to delete ServiceAccount")
	}

	rbName := fmt.Sprintf("%s-trafficschedule-viewer-binding", svc.Name)
	rb := &rbacv1.ClusterRoleBinding{ObjectMeta: metav1.ObjectMeta{Name: rbName}}
	if err := r.Delete(ctx, rb, client.PropagationPolicy(metav1.DeletePropagationBackground)); client.IgnoreNotFound(err) != nil {
		log.Error(err, "Failed to delete ClusterRoleBinding")
	}

	log.Info("Finished resource cleanup")
	return nil
}

func (r *FlavourRouterReconciler) ensureServiceAccount(ctx context.Context, svc *corev1.Service) error {
	log := ctrl.LoggerFrom(ctx).WithName("[FlavourRouter]")
	saName := fmt.Sprintf("%s-trafficschedule-viewer", svc.Name)

	sa := &corev1.ServiceAccount{
		ObjectMeta: metav1.ObjectMeta{
			Name:      saName,
			Namespace: svc.Namespace,
		},
	}

	if err := ctrl.SetControllerReference(svc, sa, r.Scheme); err != nil {
		return err
	}

	var currentSA corev1.ServiceAccount
	err := r.Get(ctx, client.ObjectKey{Name: saName, Namespace: svc.Namespace}, &currentSA)
	if err != nil {
		if apierrors.IsNotFound(err) {
			log.Info("Creating ServiceAccount", "ServiceAccount", sa.Name)
			return r.Create(ctx, sa)
		}
		return err
	}
	return nil
}

func (r *FlavourRouterReconciler) ensureClusterRoleBinding(ctx context.Context, svc *corev1.Service) error {
	log := ctrl.LoggerFrom(ctx).WithName("[FlavourRouter]")
	saName := fmt.Sprintf("%s-trafficschedule-viewer", svc.Name)
	rbName := fmt.Sprintf("%s-trafficschedule-viewer-binding", svc.Name)

	rb := &rbacv1.ClusterRoleBinding{
		ObjectMeta: metav1.ObjectMeta{
			Name:      rbName,
			Namespace: svc.Namespace,
		},
		Subjects: []rbacv1.Subject{
			{
				Kind:      "ServiceAccount",
				Name:      saName,
				Namespace: svc.Namespace,
			},
		},
		RoleRef: rbacv1.RoleRef{
			Kind:     "ClusterRole",
			Name:     "trafficschedule-viewer-role",
			APIGroup: "rbac.authorization.k8s.io",
		},
	}

	if err := ctrl.SetControllerReference(svc, rb, r.Scheme); err != nil {
		return err
	}

	var currentRB rbacv1.ClusterRoleBinding
	err := r.Get(ctx, client.ObjectKey{Name: rbName, Namespace: svc.Namespace}, &currentRB)
	if err != nil {
		if apierrors.IsNotFound(err) {
			log.Info("Creating ClusterRoleBinding", "ClusterRoleBinding", rb.Name)
			return r.Create(ctx, rb)
		}
		return err
	}
	return nil
}

func (r *FlavourRouterReconciler) ensureBufferServiceService(ctx context.Context, svc *corev1.Service, component string) error {
	log := ctrl.LoggerFrom(ctx).WithName("[FlavourRouter]")
	serviceName := fmt.Sprintf("buffer-service-%s-%s", component, svc.Name)

	labels := map[string]string{
		"app.kubernetes.io/name":       fmt.Sprintf("buffer-service-%s", component),
		"app.kubernetes.io/instance":   "carbonrouter",
		"app.kubernetes.io/component":  component,
		"app.kubernetes.io/part-of":    "carbonrouter",
		"carbonrouter/parent-service":  svc.Name,
		"app.kubernetes.io/managed-by": "carbonrouter-operator",
	}

	var ports []corev1.ServicePort
	if component == "router" {
		ports = []corev1.ServicePort{
			{Name: "http", Port: 8000, TargetPort: intstr.FromInt(8000)},
			{Name: "metrics", Port: 8001, TargetPort: intstr.FromInt(8001)},
		}
	} else { // consumer
		ports = []corev1.ServicePort{
			{Name: "metrics", Port: 8001, TargetPort: intstr.FromInt(8001)},
		}
	}

	bufferSvc := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:      serviceName,
			Namespace: svc.Namespace,
			Labels:    labels,
		},
		Spec: corev1.ServiceSpec{
			Selector: map[string]string{
				"app.kubernetes.io/name":     fmt.Sprintf("buffer-service-%s", component),
				"app.kubernetes.io/instance": "carbonrouter",
			},
			Ports: ports,
			Type:  corev1.ServiceTypeClusterIP,
		},
	}

	if err := ctrl.SetControllerReference(svc, bufferSvc, r.Scheme); err != nil {
		return err
	}

	var currentSvc corev1.Service
	err := r.Get(ctx, client.ObjectKey{Name: serviceName, Namespace: svc.Namespace}, &currentSvc)
	if err != nil {
		if apierrors.IsNotFound(err) {
			log.Info("Creating Service", "Component", component, "Service", bufferSvc.Name)
			return r.Create(ctx, bufferSvc)
		}
		return err
	}

	// Preserve ClusterIP
	bufferSvc.Spec.ClusterIP = currentSvc.Spec.ClusterIP
	if !equality.Semantic.DeepEqual(currentSvc.Spec, bufferSvc.Spec) {
		currentSvc.Spec = bufferSvc.Spec
		log.Info("Updating Service", "Component", component, "Service", bufferSvc.Name)
		return r.Update(ctx, &currentSvc)
	}

	return nil
}

func (r *FlavourRouterReconciler) ensureBufferServiceDeployment(ctx context.Context, svc *corev1.Service, component string, resources corev1.ResourceRequirements, debug bool, tsNamespace string) error {
	log := ctrl.LoggerFrom(ctx).WithName("[FlavourRouter]")
	depName := fmt.Sprintf("buffer-service-%s-%s", component, svc.Name)
	saName := fmt.Sprintf("%s-trafficschedule-viewer", svc.Name)

	labels := map[string]string{
		"app.kubernetes.io/name":       fmt.Sprintf("buffer-service-%s", component),
		"app.kubernetes.io/instance":   "carbonrouter",
		"app.kubernetes.io/component":  component,
		"app.kubernetes.io/part-of":    "carbonrouter",
		"carbonrouter/parent-service":  svc.Name,
		"app.kubernetes.io/managed-by": "carbonrouter-operator",
	}

	var annotations map[string]string
	var extraEnv []corev1.EnvVar
	podLabels := labels

	if component == "consumer" {
		annotations = map[string]string{"sidecar.istio.io/inject": "true"}
		podLabels = map[string]string{
			"app.kubernetes.io/name":       fmt.Sprintf("buffer-service-%s", component),
			"app.kubernetes.io/instance":   "carbonrouter",
			"app.kubernetes.io/component":  component,
			"app.kubernetes.io/part-of":    "carbonrouter",
			"carbonrouter/parent-service":  svc.Name,
			"app.kubernetes.io/managed-by": "carbonrouter-operator",
			"istio.io/rev":                 "default",
		}
		extraEnv = []corev1.EnvVar{
			{Name: "TARGET_SVC_SCHEME", Value: "http"},
			{Name: "TARGET_SVC_PORT", Value: "80"},
		}
	}

	baseEnv := []corev1.EnvVar{
		{Name: "RABBITMQ_URL", Value: "amqp://carbonuser:supersecret@carbonrouter-rabbitmq.carbonrouter-system.svc.cluster.local:5672"},
		{Name: "TRAFFIC_SCHEDULE_NAME", Value: "TrafficSchedule"},
		{Name: "METRICS_PORT", Value: "8001"},
		{Name: "TARGET_SVC_NAME", Value: svc.Name},
		{Name: "TARGET_SVC_NAMESPACE", ValueFrom: &corev1.EnvVarSource{FieldRef: &corev1.ObjectFieldSelector{FieldPath: "metadata.namespace"}}},
		{Name: "TS_NAME", Value: "traffic-schedule"},
		{Name: "TS_NAMESPACE", Value: tsNamespace},
		{Name: "DEBUG", Value: fmt.Sprintf("%t", debug)},
		{Name: "PYTHONUNBUFFERED", Value: "1"},
	}

	allEnv := append(baseEnv, extraEnv...)

	dep := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      depName,
			Namespace: svc.Namespace,
			Labels:    labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To[int32](1),
			Selector: &metav1.LabelSelector{
				MatchLabels: map[string]string{
					"app.kubernetes.io/name":     fmt.Sprintf("buffer-service-%s", component),
					"app.kubernetes.io/instance": "carbonrouter",
				},
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels:      podLabels,
					Annotations: annotations,
				},
				Spec: corev1.PodSpec{
					ServiceAccountName: saName,
					Containers: []corev1.Container{
						{
							Name:            fmt.Sprintf("buffer-service-%s", component),
							Image:           fmt.Sprintf("ghcr.io/belgio99/k8s-carbonrouter/buffer-service-%s:latest", component),
							ImagePullPolicy: corev1.PullAlways,
							Env:             allEnv,
							Resources:       resources,
						},
					},
				},
			},
		},
	}

	if err := ctrl.SetControllerReference(svc, dep, r.Scheme); err != nil {
		return err
	}

	var currentDep appsv1.Deployment
	err := r.Get(ctx, client.ObjectKey{Name: depName, Namespace: svc.Namespace}, &currentDep)
	if err != nil {
		if apierrors.IsNotFound(err) {
			log.Info("Creating Deployment", "Component", component, "Deployment", dep.Name)
			return r.Create(ctx, dep)
		}
		return err
	}

	desired := dep.Spec
	desired.Replicas = nil

	current := currentDep.Spec
	current.Replicas = nil

	if !equality.Semantic.DeepEqual(current, desired) {
		log.Info("Updating Deployment", "Component", component, "Deployment", dep.Name)

		return retry.RetryOnConflict(retry.DefaultRetry, func() error {
			var latest appsv1.Deployment
			if err := r.Get(ctx, client.ObjectKey{Name: depName, Namespace: svc.Namespace}, &latest); err != nil {
				return err
			}
			// manteniamo le Replicas attuali (gestite dall’HPA/KEDA)
			dep.Spec.Replicas = latest.Spec.Replicas

			latest.Spec = dep.Spec
			return r.Update(ctx, &latest)
		})
	}

	return nil
}

func (r *FlavourRouterReconciler) ensureRouterScaledObject(ctx context.Context, svc *corev1.Service, autoscaling schedulingv1alpha1.AutoscalingConfig, replicaCeilings map[string]int32) error {
	log := ctrl.LoggerFrom(ctx).WithName("[FlavourRouter]")
	soName := fmt.Sprintf("buffer-service-router-%s", svc.Name)
	targetName := fmt.Sprintf("buffer-service-router-%s", svc.Name)

	// Router is exempt from carbon-aware throttling to ensure incoming traffic is always handled
	// Queue accumulation happens downstream in consumers/targets during high carbon periods
	maxReplicas := autoscaling.MaxReplicaCount
	componentName := "router"
	// NOTE: Router scaling ceiling is NOT applied - router scales freely based on load
	// This is intentional: router must accept all incoming requests to prevent client failures
	log.Info("Router scaling freely (exempt from carbon-aware ceiling)", "component", componentName, "maxReplicas", *maxReplicas)

	so := &kedav1alpha1.ScaledObject{
		ObjectMeta: metav1.ObjectMeta{
			Name:      soName,
			Namespace: svc.Namespace,
			Labels: map[string]string{
				parentServiceLabel: svc.Name,
			},
		},
		Spec: kedav1alpha1.ScaledObjectSpec{
			ScaleTargetRef:  &kedav1alpha1.ScaleTarget{Name: targetName},
			PollingInterval: ptr.To[int32](5),
			CooldownPeriod:  autoscaling.CooldownPeriod,
			MinReplicaCount: autoscaling.MinReplicaCount,
			MaxReplicaCount: maxReplicas,
			Triggers: []kedav1alpha1.ScaleTriggers{
				{
					Type: "cpu",
					Metadata: map[string]string{
						"type":  "Utilization",
						"value": fmt.Sprintf("%d", *autoscaling.CPUUtilization),
					},
				},
			},
		},
	}

	if err := ctrl.SetControllerReference(svc, so, r.Scheme); err != nil {
		return err
	}

	var currentSO kedav1alpha1.ScaledObject
	err := r.Get(ctx, client.ObjectKey{Name: soName, Namespace: svc.Namespace}, &currentSO)
	if err != nil {
		if apierrors.IsNotFound(err) {
			log.Info("Creating Router ScaledObject", "ScaledObject", so.Name)
			return r.Create(ctx, so)
		}
		return err
	}

	if !equality.Semantic.DeepEqual(currentSO.Spec, so.Spec) {
		currentSO.Spec = so.Spec
		log.Info("Updating Router ScaledObject", "ScaledObject", so.Name)
		return r.Update(ctx, &currentSO)
	}

	return nil
}

func (r *FlavourRouterReconciler) ensureConsumerScaledObject(ctx context.Context, svc *corev1.Service, autoscaling schedulingv1alpha1.AutoscalingConfig, precisions []int, replicaCeilings map[string]int32) error {
	log := ctrl.LoggerFrom(ctx).WithName("[FlavourRouter]")
	soName := fmt.Sprintf("buffer-service-consumer-%s", svc.Name)
	targetName := fmt.Sprintf("buffer-service-consumer-%s", svc.Name)

	// Apply carbon-aware replica ceiling if available
	maxReplicas := autoscaling.MaxReplicaCount
	componentName := "consumer"
	if ceiling, ok := replicaCeilings[componentName]; ok && ceiling > 0 {
		// Use the carbon-aware ceiling, but respect the configured max as an upper bound
		if autoscaling.MaxReplicaCount != nil && ceiling < *autoscaling.MaxReplicaCount {
			maxReplicas = &ceiling
			log.Info("Applying carbon-aware replica ceiling", "component", componentName, "ceiling", ceiling, "original", *autoscaling.MaxReplicaCount)
		}
	}

	rabbitmqTriggers := make([]kedav1alpha1.ScaleTriggers, 0, len(precisions))
	for _, precision := range precisions {
		rabbitmqTriggers = append(rabbitmqTriggers, kedav1alpha1.ScaleTriggers{
			Type:              "rabbitmq",
			AuthenticationRef: &kedav1alpha1.AuthenticationRef{Name: "carbonrouter-rabbitmq-auth", Kind: "ClusterTriggerAuthentication"},
			Metadata: map[string]string{
				"queueName": directQueueName(svc.Namespace, svc.Name, precision),
				"mode":      "QueueLength",
				"value":     "500",
			},
		})
	}

	queueRegex := fmt.Sprintf(`^%s\\.%s\\.queue\\.precision-`, svc.Namespace, svc.Name)

	so := &kedav1alpha1.ScaledObject{
		ObjectMeta: metav1.ObjectMeta{
			Name:      soName,
			Namespace: svc.Namespace,
			Labels: map[string]string{
				parentServiceLabel: svc.Name,
			},
		},
		Spec: kedav1alpha1.ScaledObjectSpec{
			ScaleTargetRef:  &kedav1alpha1.ScaleTarget{Name: targetName},
			PollingInterval: ptr.To[int32](5),
			CooldownPeriod:  autoscaling.CooldownPeriod,
			MinReplicaCount: autoscaling.MinReplicaCount,
			MaxReplicaCount: maxReplicas,
			Triggers: append(rabbitmqTriggers,
				kedav1alpha1.ScaleTriggers{
					Type: "cpu",
					Metadata: map[string]string{
						"type":  "Utilization",
						"value": fmt.Sprintf("%d", *autoscaling.CPUUtilization),
					},
				},
				kedav1alpha1.ScaleTriggers{
					Type: "prometheus",
					Metadata: map[string]string{
						"serverAddress":       "http://carbonrouter-kube-prometheu-prometheus.carbonrouter-system.svc:9090",
						"query":               "sum(increase(consumer_http_requests_created[60s]))",
						"threshold":           "500",
						"activationThreshold": "1",
					},
				},
				kedav1alpha1.ScaleTriggers{
					Type: "prometheus",
					Metadata: map[string]string{
						"serverAddress": "http://carbonrouter-kube-prometheu-prometheus.carbonrouter-system.svc:9090",
						"query":         fmt.Sprintf(`sum(rabbitmq_queue_messages_ready{queue=~"%s.+"})`, queueRegex),
						"threshold":     "1",
					},
				},
			),
		},
	}

	if err := ctrl.SetControllerReference(svc, so, r.Scheme); err != nil {
		return err
	}

	var currentSO kedav1alpha1.ScaledObject
	err := r.Get(ctx, client.ObjectKey{Name: soName, Namespace: svc.Namespace}, &currentSO)
	if err != nil {
		if apierrors.IsNotFound(err) {
			log.Info("Creating Consumer ScaledObject", "ScaledObject", so.Name)
			return r.Create(ctx, so)
		}
		return err
	}

	if !equality.Semantic.DeepEqual(currentSO.Spec, so.Spec) {
		currentSO.Spec = so.Spec
		log.Info("Updating Consumer ScaledObject", "ScaledObject", so.Name)
		return r.Update(ctx, &currentSO)
	}

	return nil
}

func (r *FlavourRouterReconciler) ensurePrecisionScaledObject(ctx context.Context, svc *corev1.Service, precision int, targetName string, autoscaling schedulingv1alpha1.AutoscalingConfig, replicaCeilings map[string]int32) error {
	log := ctrl.LoggerFrom(ctx).WithName("[FlavourRouter]")
	if targetName == "" {
		return fmt.Errorf("missing deployment name for precision %d", precision)
	}

	soName := fmt.Sprintf("%s-precision-%d", svc.Name, precision)
	directQueue := directQueueName(svc.Namespace, svc.Name, precision)
	bufferedQueue := bufferedQueueName(svc.Namespace, svc.Name, precision)

	// Apply carbon-aware replica ceiling if available
	// All precision deployments share the "target" component ceiling
	maxReplicas := autoscaling.MaxReplicaCount
	componentName := "target"
	if ceiling, ok := replicaCeilings[componentName]; ok && ceiling > 0 {
		// Use the carbon-aware ceiling, but respect the configured max as an upper bound
		if autoscaling.MaxReplicaCount != nil && ceiling < *autoscaling.MaxReplicaCount {
			maxReplicas = &ceiling
			log.Info("Applying carbon-aware replica ceiling", "component", componentName, "target", targetName, "precision", precision, "ceiling", ceiling, "original", *autoscaling.MaxReplicaCount)
		}
	}

	so := &kedav1alpha1.ScaledObject{
		ObjectMeta: metav1.ObjectMeta{
			Name:      soName,
			Namespace: svc.Namespace,
			Labels: map[string]string{
				parentServiceLabel: svc.Name,
			},
		},
		Spec: kedav1alpha1.ScaledObjectSpec{
			ScaleTargetRef:  &kedav1alpha1.ScaleTarget{Name: targetName},
			PollingInterval: ptr.To[int32](5),
			CooldownPeriod:  autoscaling.CooldownPeriod,
			MinReplicaCount: autoscaling.MinReplicaCount,
			MaxReplicaCount: maxReplicas,
			Triggers: []kedav1alpha1.ScaleTriggers{
				{
					Type: "prometheus",
					Metadata: map[string]string{
						"serverAddress":       "http://carbonrouter-kube-prometheu-prometheus.carbonrouter-system.svc:9090",
						"query":               fmt.Sprintf(`sum(max_over_time(rabbitmq_queue_messages_ready{queue="%s"}[30s]))`, bufferedQueue),
						"threshold":           "500",
						"activationThreshold": "1",
					},
				},
				{
					Type:              "rabbitmq",
					AuthenticationRef: &kedav1alpha1.AuthenticationRef{Name: "carbonrouter-rabbitmq-auth", Kind: "ClusterTriggerAuthentication"},
					Metadata: map[string]string{
						"queueName": directQueue,
						"mode":      "QueueLength",
						"value":     "500",
					},
				},
				{
					Type: "cpu",
					Metadata: map[string]string{
						"type":  "Utilization",
						"value": fmt.Sprintf("%d", *autoscaling.CPUUtilization),
					},
				},
			},
		},
	}

	if err := ctrl.SetControllerReference(svc, so, r.Scheme); err != nil {
		return err
	}

	var currentSO kedav1alpha1.ScaledObject
	err := r.Get(ctx, client.ObjectKey{Name: soName, Namespace: svc.Namespace}, &currentSO)
	if err != nil {
		if apierrors.IsNotFound(err) {
			log.Info("Creating Precision ScaledObject", "ScaledObject", so.Name)
			return r.Create(ctx, so)
		}
		return err
	}

	if !equality.Semantic.DeepEqual(currentSO.Spec, so.Spec) {
		currentSO.Spec = so.Spec
		log.Info("Updating Precision ScaledObject", "ScaledObject", so.Name)
		return r.Update(ctx, &currentSO)
	}

	return nil
}
