# OpenBedside cloud demo: gateway + simulated pump + stand-in EHR, one public HTTPS port.
# Build from the REPOSITORY ROOT:  docker build -f deploy/cloud/Dockerfile -t openbedside-demo .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    BIND=0.0.0.0 \
    PORT=10000

COPY . /src
RUN pip install --no-cache-dir /src \
 && useradd --system --uid 10001 openbedside \
 && mkdir -p /data && chown openbedside /data

COPY deploy/cloud/ /opt/openbedside-cloud/
WORKDIR /data
USER openbedside
EXPOSE 10000
CMD ["python3", "/opt/openbedside-cloud/start.py"]
