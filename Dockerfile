FROM tiangolo/meinheld-gunicorn-flask:python3.8-alpine3.11
COPY ./doorman /app/doorman
COPY requirements.txt /app
RUN pip install --upgrade pip
RUN pip install -r /app/requirements.txt

ENV MODULE_NAME=doorman.app
