# One image, one container per team. Build from the repository root:
#   docker build -t smash-bots-sandbox -f sandbox/Dockerfile .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY sandbox/requirements.txt /tmp/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev git \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && apt-get purge -y --auto-remove gcc libc6-dev git \
    && rm -rf /var/lib/apt/lists/* /tmp/requirements.txt

COPY sandbox/bot_host.py sandbox/check_bot.py /sandbox/
COPY docs/libmelee/gamestate.md docs/libmelee/controller.md docs/libmelee/enums.md \
     docs/libmelee/framedata.md docs/libmelee/stages.md /docs/libmelee/
COPY server/chat_context/rules.md /docs/RULES.md

RUN useradd --create-home bot && mkdir /workspace && chown bot:bot /workspace
USER bot
WORKDIR /workspace

CMD ["sleep", "infinity"]
