FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data dirs in case they aren't mounted
RUN mkdir -p data/ncaidshp/full

EXPOSE 8930

CMD ["gunicorn", \
     "--bind", "0.0.0.0:8930", \
     "--workers", "2", \
     "--worker-class", "gthread", \
     "--threads", "4", \
     "--timeout", "180", \
     "--keep-alive", "5", \
     "wsgi:app"]
