FROM python:3.14.10-slim
LABEL org.opencontainers.image.source="https://github.com/dimonb/jone"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY --chown=10001:10001 game.py words.py server.py ./
COPY --chown=10001:10001 static ./static
USER 10001:10001
EXPOSE 8080
CMD ["python3", "server.py"]
