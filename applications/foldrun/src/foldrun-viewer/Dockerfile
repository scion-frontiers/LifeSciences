# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# AlphaFold Structure Viewer - Cloud Run Dockerfile

# Stage 1: Vendor JS dependencies via npm
FROM node:18-slim AS npm-deps
WORKDIR /npm
COPY package.json .
RUN npm install --production

# Stage 2: Build environment (Python)
FROM python:3.13-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /uvx /bin/

WORKDIR /app

# Copy config files and lock
COPY pyproject.toml uv.lock ./

# Install dependencies (frozen, no-dev, no-install-project)
RUN uv sync --frozen --no-install-project --no-dev --no-editable

# Stage 3: Runtime image
FROM python:3.13-slim

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy application files
COPY app.py .
COPY templates/ templates/
COPY static/ static/

# Copy vendor JS libraries from npm stage
COPY --from=npm-deps /npm/node_modules/3dmol/build/3Dmol-min.js static/js/3Dmol-min.js
COPY --from=npm-deps /npm/node_modules/marked/marked.min.js static/js/marked.min.js

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app && chmod o+w /app
USER appuser

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD exec gunicorn --bind :$PORT --workers 2 --threads 4 --timeout 0 app:app
