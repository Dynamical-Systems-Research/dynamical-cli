# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ARG WHEEL_FILE=dynamical-0.1.0-py3-none-any.whl

COPY --from=wheel ${WHEEL_FILE} /tmp/${WHEEL_FILE}
COPY --from=demo requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir /tmp/${WHEEL_FILE} \
    -r /tmp/requirements.txt \
    && rm /tmp/${WHEEL_FILE} /tmp/requirements.txt

RUN useradd --create-home --uid 10001 luna
RUN install -d -m 0555 /opt/luna-demo
COPY --chmod=0444 --from=demo run_demo.py agent-prompt.md objective.yaml /opt/luna-demo/

ENV HOME=/workspace \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /workspace
USER 10001:10001

ENTRYPOINT ["python", "/opt/luna-demo/run_demo.py", "--workspace", "/workspace", "--prompt", "/opt/luna-demo/agent-prompt.md", "--objective", "/opt/luna-demo/objective.yaml"]
