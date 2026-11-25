# Sandbox image for running LLM-written stage code (requirement 5).
#
# The image contains only the data libraries stage code may use, pinned to the same
# versions as the host so results are reproducible (requirement 18, mitigation 10).
# The harness itself is mounted read-only at run time from src/adpb/sandbox_runtime.
#
# Isolation is applied by `docker run` flags in src/adpb/sandbox/docker_runner.py:
#   --network none, --read-only root, tmpfs /tmp, --cpus, --memory, --pids-limit,
#   --cap-drop ALL, --security-opt no-new-privileges, non-root --user, host-side timeout.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/opt/adpb_runtime

RUN pip install \
        pandas==3.1.1 \
        numpy==2.5.5 \
        pyarrow==26.0.1 \
        duckdb==2.0.1 \
    && useradd --uid 10001 --create-home sandbox \
    && rm -rf /root/.cache

USER 10001
WORKDIR /work
CMD ["python", "--version"]
