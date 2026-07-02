FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY hyperfurion_relay ./hyperfurion_relay
RUN pip install --no-cache-dir .
# Persist the subscriber DB outside the container filesystem.
ENV RELAY_DB=/data/relay.db
VOLUME /data
EXPOSE 8787
CMD ["hyperfurion-relay"]
