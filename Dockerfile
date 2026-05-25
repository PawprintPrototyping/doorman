FROM python:3.14-alpine3.23

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY ./doorman ./doorman
COPY ./websocket_client.py .
COPY ./main.py .

EXPOSE 5000

# Keep --workers at 1 (uvicorn's default). Each worker spawns its own
# MemberMatters websocket client; multiple workers would produce duplicate
# door_access events.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000"]
