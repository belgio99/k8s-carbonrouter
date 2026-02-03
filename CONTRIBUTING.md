# Contributing to K8s-CarbonRouter

Thank you for your interest in contributing to K8s-CarbonRouter!

## Getting Started

1. Fork the repository
2. Clone your fork locally
3. Create a new branch for your feature or fix

## Development Setup

### Prerequisites

- Kubernetes cluster (Kind or K3s recommended for local development)
- Go 1.23+
- Python 3.11+
- Helm 3.x
- Docker

### Components

- **Operator** (`operator/`): Go-based controller using Kubebuilder
- **Buffer Service** (`buffer-service/`): FastAPI router and consumer
- **Decision Engine** (`decision-engine/`): Python scheduling service
- **Carbonstat** (`carbonstat/`): Sample Flask application

### Running Tests

```bash
# Operator tests
cd operator && make test

# Python components
cd buffer-service && pytest
cd decision-engine && pytest
```

## Submitting Changes

1. Ensure your code follows the existing style
2. Update documentation if needed
3. Run tests before submitting
4. Create a pull request with a clear description

## Reporting Issues

- Use GitHub Issues for bug reports and feature requests
- Include relevant logs, Kubernetes version, and steps to reproduce

## Questions

Feel free to open an issue for any questions about the project.
