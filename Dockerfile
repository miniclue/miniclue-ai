# 1. Builder stage
FROM python:3.13-alpine AS builder

# Install poetry
RUN pip install poetry

# Set working directory
WORKDIR /app

# Copy dependency files
COPY poetry.lock pyproject.toml ./

# Configure poetry to create venv in project directory
RUN poetry config virtualenvs.in-project true

# Install dependencies
RUN poetry install --without dev --no-root --no-interaction --no-ansi

# 2. Final stage
FROM python:3.13-alpine

# Set working directory
WORKDIR /app

# Copy virtual env from builder
COPY --from=builder /app/.venv /.venv

# Activate virtual env
ENV PATH="/app/.venv/bin:$PATH"

# Copy source code
COPY ./app ./app

# Expose port and run application
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"] 