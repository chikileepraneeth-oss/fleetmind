# The FleetMind public demo: the live website with the 2D map and the 3D view.
#
#   docker build -t fleetmind-demo .
#   docker run --rm -p 8000:8000 fleetmind-demo        # then open http://127.0.0.1:8000
#
# Public mode: one shared warehouse, no MCP tools, no AI, no API keys (see web/server.py).
# The robots still talk VDA 5050 messages, over the in-memory bus (no separate broker needed,
# so it runs on hosts that allow only one container, like Hugging Face Spaces or Cloud Run).
# Hosts set PORT themselves; 8000 is the default.

FROM python:3.12-slim

# LightGBM (the forecast) needs the OpenMP library
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# run as a normal user, not root
RUN useradd --create-home fleetmind
WORKDIR /home/fleetmind/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=fleetmind:fleetmind . .
USER fleetmind

# FLEETMIND_MESSAGES=on: the robots talk VDA 5050 messages (over the in-memory bus).
# off: the same warehouse without the message layer, about 2x faster: for a very small CPU
# (Render's free plan has 0.1 CPU: about 2 ticks per second with messages, 5 without).
# EMBED_FROM: the website that may show the demo inside its pages, e.g. https://you.netlify.app
ENV PORT=8000 PYTHONUNBUFFERED=1 FLEETMIND_MESSAGES=on EMBED_FROM=
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')"

CMD ["sh", "-c", "python -m web.server --public $( [ \"$FLEETMIND_MESSAGES\" = off ] || echo --mqtt memory ) --host 0.0.0.0 --port ${PORT} --no-browser --scenario realistic_friday"]
