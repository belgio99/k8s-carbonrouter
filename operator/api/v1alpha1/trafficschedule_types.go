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
package v1alpha1

import (
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// EDIT THIS FILE!  THIS IS SCAFFOLDING FOR YOU TO OWN!
// NOTE: json tags are required.  Any new fields you add must have json tags for the fields to be serialized.

// AutoscalingConfig defines the autoscaling parameters for a component.
type AutoscalingConfig struct {
	// +optional
	MinReplicaCount *int32 `json:"minReplicaCount,omitempty"`
	MaxReplicaCount *int32 `json:"maxReplicaCount,omitempty"`
	// +optional
	CooldownPeriod *int32 `json:"cooldownPeriod,omitempty"`
	// +optional
	CPUUtilization *int32 `json:"cpuUtilization,omitempty"`
}

// ComponentConfig defines the configuration for a specific component like router or consumer.
type ComponentConfig struct {
	// +optional
	Autoscaling AutoscalingConfig `json:"autoscaling,omitempty"`
	// +optional
	Resources corev1.ResourceRequirements `json:"resources,omitempty"`
	// +optional
	Debug bool `json:"debug,omitempty"`
}

// SchedulerConfigSpec defines runtime tuning knobs for the credit scheduler.
type SchedulerConfigSpec struct {
	// +optional
	TargetError *string `json:"targetError,omitempty"`
	// +optional
	CreditMin *string `json:"creditMin,omitempty"`
	// +optional
	CreditMax *string `json:"creditMax,omitempty"`
	// +optional
	CreditWindow *int32 `json:"creditWindow,omitempty"`
	// +optional
	Policy *string `json:"policy,omitempty"`
	// +optional
	ValidFor *int32 `json:"validFor,omitempty"`
	// +optional
	DiscoveryInterval *int32 `json:"discoveryInterval,omitempty"`
	// +optional
	CarbonTarget *string `json:"carbonTarget,omitempty"`
	// +optional
	CarbonTimeout *int32 `json:"carbonTimeout,omitempty"`
	// +optional
	CarbonCacheTTL *int32 `json:"carbonCacheTTL,omitempty"`
}

// TargetConfig defines the configuration for the target deployments.
type TargetConfig struct {
	// +optional
	Autoscaling AutoscalingConfig `json:"autoscaling,omitempty"`
}

// TrafficScheduleSpec defines the desired state of TrafficSchedule.
type TrafficScheduleSpec struct {
	// INSERT ADDITIONAL SPEC FIELDS - desired state of cluster
	// Important: Run "make" to regenerate code after modifying this file

	// +optional
	Target TargetConfig `json:"target,omitempty"`
	// +optional
	Router ComponentConfig `json:"router,omitempty"`
	// +optional
	Consumer ComponentConfig `json:"consumer,omitempty"`
	// +optional
	Scheduler SchedulerConfigSpec `json:"scheduler,omitempty"`
}

// StrategyDecision describes the scheduler outcome for a specific precision level.
type StrategyDecision struct {
	// Precision is expressed as an integer percentage (e.g. 100, 85, 60).
	Precision int `json:"precision"`
	// Weight represents the share of traffic (percentage) assigned to this precision.
	Weight int `json:"weight"`
}

// FlavourRule describes routing weights for router consumers.
type FlavourRule struct {
	FlavourName string `json:"flavourName"`
	Precision   int    `json:"precision"`
	Weight      int    `json:"weight"`
}

// TrafficScheduleStatus defines the observed state of TrafficSchedule.
type TrafficScheduleStatus struct {
	// Strategies contains the routing weights for each known precision level.
	Strategies []StrategyDecision `json:"strategies"`
	// FlavourRules provides backward-compatible data for router components.
	FlavourRules []FlavourRule `json:"flavourRules,omitempty"`
	// ActivePolicy indicates the policy currently selected by the decision engine.
	ActivePolicy string `json:"activePolicy"`
	// ValidUntil specifies when the schedule should be refreshed.
	ValidUntil metav1.Time `json:"validUntil"`
	// CreditBalance exposes the current credit balance maintained by the scheduler.
	CreditBalance string `json:"creditBalance,omitempty"`
	// CreditVelocity represents the average rate of change of the credit balance.
	CreditVelocity string `json:"creditVelocity,omitempty"`
	// CreditTarget denotes the configured precision error target.
	CreditTarget string `json:"creditTarget,omitempty"`
	// CreditMin exposes the lower bound applied to the credit ledger.
	CreditMin string `json:"creditMin,omitempty"`
	// CreditMax exposes the upper bound applied to the credit ledger.
	CreditMax string `json:"creditMax,omitempty"`
	// ProcessingThrottle exports the throttle factor applied to downstream autoscaling.
	ProcessingThrottle string `json:"processingThrottle,omitempty"`
	// EffectiveReplicaCeilings exposes throttled replica limits keyed by component name.
	EffectiveReplicaCeilings map[string]int32 `json:"effectiveReplicaCeilings,omitempty"`
	// CarbonIndex reflects the current qualitative carbon intensity label.
	CarbonIndex string `json:"carbonIndex,omitempty"`
	// CarbonForecastNow is the current slot forecast in gCO2/kWh.
	CarbonForecastNow string `json:"carbonForecastNow,omitempty"`
	// CarbonForecastNext is the next slot forecast in gCO2/kWh.
	CarbonForecastNext string `json:"carbonForecastNext,omitempty"`
	// ForecastSchedule summarises the upcoming half-hour slots as reported by the provider.
	ForecastSchedule []ForecastSlot `json:"forecastSchedule,omitempty"`
	// Diagnostics contains policy-specific telemetry useful for debugging.
	Diagnostics map[string]string `json:"diagnostics,omitempty"`
}

// ForecastSlot describes a single carbon forecast interval.
type ForecastSlot struct {
	From     string `json:"from"`
	To       string `json:"to"`
	Forecast string `json:"forecast"`
	// +optional
	Index string `json:"index,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Namespaced

// TrafficSchedule is the Schema for the trafficschedules API.
type TrafficSchedule struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   TrafficScheduleSpec   `json:"spec,omitempty"`
	Status TrafficScheduleStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// TrafficScheduleList contains a list of TrafficSchedule.
type TrafficScheduleList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []TrafficSchedule `json:"items"`
}

func init() {
	SchemeBuilder.Register(&TrafficSchedule{}, &TrafficScheduleList{})
}
