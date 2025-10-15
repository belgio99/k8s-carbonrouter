FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application entrypoint and local modules
COPY decision-engine.py ./
COPY scheduler ./scheduler

EXPOSE 5001

CMD ["python", "decision-engine.py"]