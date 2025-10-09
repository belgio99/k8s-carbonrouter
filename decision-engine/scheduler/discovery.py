"""Strategy discovery helpers for Kubernetes."""

from __future__ import annotations

import logging
import os
from typing import Iterable, List

from kubernetes import client, config
from kubernetes.config import ConfigException

from .models import StrategyProfile

_LOGGER = logging.getLogger("scheduler.discovery")


class KubeStrategyDiscoverer:
    """Discovers strategy deployments annotated with carbonstat metadata."""

    def __init__(self, namespace: str | None = None) -> None:
        try:
            config.load_incluster_config()
        except ConfigException:
            config.load_kube_config()
        self._api = client.AppsV1Api()
        self.namespace = namespace or os.getenv("TARGET_SVC_NAMESPACE")

    def discover(self) -> List[StrategyProfile]:
        label_selector = "carbonstat.precision"
        if self.namespace:
            deployments = self._api.list_namespaced_deployment(
                namespace=self.namespace,
                label_selector=label_selector,
            )
        else:
            deployments = self._api.list_deployment_for_all_namespaces(
                label_selector=label_selector
            )

        strategies: List[StrategyProfile] = []
        for deployment in deployments.items:
            labels = deployment.metadata.labels or {}
            precision_label = labels.get("carbonstat.precision")
            if not precision_label:
                continue
            try:
                precision_value = float(precision_label)
                if precision_value > 1:
                    precision_value /= 100.0
            except ValueError:
                _LOGGER.warning(
                    "Deployment %s/%s has invalid precision label: %s",
                    deployment.metadata.namespace,
                    deployment.metadata.name,
                    precision_label,
                )
                continue

            name = labels.get("carbonstat.strategy") or deployment.metadata.name
            carbon_label = labels.get("carbonstat.emissions")
            try:
                carbon_intensity = float(carbon_label) if carbon_label else 0.0
            except ValueError:
                carbon_intensity = 0.0

            deadline_label = labels.get("carbonstat.deadline")
            try:
                deadline = int(deadline_label) if deadline_label else 120
            except ValueError:
                deadline = 120

            strategies.append(
                StrategyProfile(
                    name=name,
                    precision=precision_value,
                    carbon_intensity=carbon_intensity,
                    deadline=deadline,
                    enabled=True,
                    annotations=labels,
                )
            )

        if not strategies:
            _LOGGER.warning("No carbon strategies discovered via Kubernetes")
        else:
            strategies.sort(key=lambda s: s.precision, reverse=True)
        return strategies

    def close(self) -> None:
        if self._api.api_client:
            self._api.api_client.close()


def merge_strategies(primary: Iterable[StrategyProfile], fallback: Iterable[StrategyProfile]) -> List[StrategyProfile]:
    """Combine discovered strategies with fallbacks, preferring primary."""

    merged: dict[str, StrategyProfile] = {s.name: s for s in fallback}
    for strategy in primary:
        merged[strategy.name] = strategy
    return list(merged.values())
