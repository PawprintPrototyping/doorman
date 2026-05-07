FROM tiangolo/meinheld-gunicorn-flask:python3.9
COPY ./doorman /app/doorman
COPY ./websocket_client.py /app
COPY ./main.py /app
COPY requirements.txt /app
RUN pip install --upgrade pip
RUN pip install -r /app/requirements.txt

ENV MODULE_NAME=main
ENV WEB_CONCURRENCY=1
