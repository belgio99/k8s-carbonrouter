# Carbonstat

Carbonstat is an open-source prototype for configuring and optimising carbon-aware software services. It selects execution strategies to minimise carbon emissions while maintaining a target quality of service.

Carbonstat methodology is described in the following article:

> [Stefano Forti](http://pages.di.unipi.it/forti), [Jacopo Soldani](http://pages.di.unipi.it/soldani), [Antonio Brogi](http://pages.di.unipi.it/brogi)<br>
> [**Carbon-aware Software Services**](https://doi.org/10.1007/978-3-031-84617-5_6), <br> 
> *11th European Conference On Service-Oriented And Cloud Computing (ESOCC), 2025*

If you wish to reuse source code in this repo, please consider citing it.

## Features of carbonstat

- Implementation based on the Strategy pattern
- Carbon-aware optimization based on forecasted carbon intensity and request rates
- Configurable trade-off between energy consumption and output quality
- Open-source Python prototype using Google OR-Tools

## Strategies

Strategies live under `flavours/` and implement the `CarbonAwareStrategy`
interface. Each strategy defines two methods:

- `nop()` – returns an identifier included in responses and metrics.
- `avg(data)` – computes the mean according to the strategy's sampling rules.

| Strategy | Key (`FLAVOUR`) | Identifier | Behaviour |
| -------- | --------------- | ---------- | --------- |
| `HighPowerStrategy` | `high` | `HIGH_POWER` | Uses the full dataset to provide the exact arithmetic mean. |
| `MediumPowerStrategy` | `mid` | `MEDIUM_POWER` | Samples every other element (approx. 50 percent of the input). |
| `LowPowerStrategy` | `low` | `LOW_POWER` | Samples one element out of four (approx. 25 percent of the input). |

## API

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/avg` | Accepts `{ "numbers": [...] }` and returns `{ "avg": <value>, "elapsed": <ms>, "strategy": <id> }`. |
| `GET` | `/healthz` | Returns `200 OK` if the process is ready. |

The WSGI server listens on `0.0.0.0:80` by default.

## Environment Variables

| Name | Default | Description |
| ---- | ------- | ----------- |
| `FLAVOUR` | `high` | Chooses the execution strategy (`high`, `mid`, or `low`). |

## Running Locally

```bash
cd carbonstat
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
export FLAVOUR=high
python carbon-aware-service.py
```

Test the endpoint:

```bash
curl -X POST http://localhost:80/avg \
  -H "Content-Type: application/json" \
  -d '{"numbers": [1, 2, 3, 4, 5]}'
```

## Docker Image

```bash
docker build -t carbonstat:dev .
```
