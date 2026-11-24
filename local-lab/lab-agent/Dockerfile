FROM docker:27-cli

RUN apk add --no-cache python3 py3-pip py3-requests docker-cli-compose

WORKDIR /app
COPY contract.py /app/contract.py
COPY agent.py /app/agent.py

ENV PYTHONUNBUFFERED=1
ENV LAB_AGENT_BIND_HOST=0.0.0.0
ENV LAB_AGENT_PORT=8085
ENV LAB_COMPOSE_DIR=/local-lab

EXPOSE 8085
CMD ["python3", "/app/agent.py"]
