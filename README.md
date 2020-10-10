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

# Pre-commit hooks (for development)
pip install pre-commit && pre-commit install

FLASK_ENVIRONMENT=debug flask run
```
