# Keep the provider version fixed; the patch must be reviewed on upgrades.
FROM docker.io/searxng/searxng:2026.9.8-3fdc6d753

COPY duckduckgo.patch /tmp/duckduckgo.patch
COPY test_duckduckgo.py /tmp/test_duckduckgo.py
# Upstream ships bytecode; discard it so the patched source is executed.
RUN cd /usr/local/searxng \
    && patch -p1 < /tmp/duckduckgo.patch \
    && rm -f searx/engines/__pycache__/duckduckgo.*.pyc \
    && PYTHONPATH=/usr/local/searxng .venv/bin/python /tmp/test_duckduckgo.py \
    && rm /tmp/duckduckgo.patch /tmp/test_duckduckgo.py
