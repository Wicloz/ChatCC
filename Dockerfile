FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY lua/    ./lua/

EXPOSE 8080

# Single worker on purpose: the chat-source registry (one upstream streamList
# connection per liveChatId, shared across all clients) lives in-process.
# Extra workers would each open their own upstream connection for the same chat,
# multiplying YouTube API quota use. Chat traffic is light, so one asyncio
# worker comfortably handles many concurrent WebSockets.
CMD ["uvicorn", "main:app", "--app-dir", "/app/server", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
