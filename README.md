# doorman
A solution for connecting the Fanvil i31s to FreeIPA

# Quickstart

Required:
 * python 3.7+

Suggested:
 * [direnv](https://direnv.net/docs/installation.html)

```sh
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependancies
pip install -r requirements.txt

# Edit configuration template:
cp envrc.example .envrc

# Pre-commit hooks (for development)
pip install pre-commit && pre-commit install

FLASK_APP=doorman FLASK_ENV=development flask run --host=0.0.0.0
```

# Deploy with Docker

```sh
# 1. Build the image:
docker build -t doorman .

# 2. Put the environment variables from envrc.example in .env
cp envrc.example .env

# 3. Edit your config to suit

# 4. Run with:
docker run -d -p 5000:80 --name=doorman --env-file=.env doorman:latest
```
