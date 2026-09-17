FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY bouch_registry/ ./bouch_registry/
RUN pip install --no-cache-dir .

# The registry dataset is not part of the wheel: it is data the service serves,
# passed explicitly with --data so the running container can never fall back to
# some other copy.
COPY registry/ ./registry/

EXPOSE 8080

CMD ["bouch-registry", "--data", "/app/registry", "serve", "--host", "0.0.0.0"]
