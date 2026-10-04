# Build from the repository root:  docker build -f backend/Dockerfile -t insightflow-api .

FROM python:3.12-slim

# Print logs straight away instead of buffering them.
ENV PYTHONUNBUFFERED=1

# The code lives in backend/, next to demo_data/. Some scripts look for demo_data/ one level
# above backend/, so keep the same layout inside the image.
WORKDIR /app/backend

# Install the dependencies first. They change rarely, so Docker can reuse this step
# when only the code has changed.
RUN pip install uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen

# Use the virtual environment that uv created.
ENV PATH="/app/backend/.venv/bin:$PATH"

# Now the code, and the demo data the seed script loads.
COPY backend/ ./
COPY demo_data /app/demo_data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
