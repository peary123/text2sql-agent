# Serves the text-to-SQL API on port 8000.
#
# The Spider databases are NOT baked into the image: they are ~1 GB and not
# ours to redistribute. Mount them at run time:
#
#   docker build -t text2sql-agent .
#   docker run -p 8000:8000 -v "$PWD/data:/app/data:ro" \
#       -e OPENAI_API_KEY=... text2sql-agent
#
# Read-only is enough -- every query already runs on a read-only connection.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so a code change does not re-run pip.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only what the service runs. Explicit copies, so nothing else -- .env in
# particular -- can end up in an image layer by accident.
COPY src/ src/
COPY scripts/serve.py scripts/serve.py

EXPOSE 8000

# 0.0.0.0, not the local default: inside a container, 127.0.0.1 is unreachable
# from the host.
CMD ["python", "scripts/serve.py", "--host", "0.0.0.0", "--port", "8000"]
