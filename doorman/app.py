#!/usr/bin/env python
import os
import socket
import threading

import ldap3
import requests
from flask import Flask, request
from requests.auth import HTTPBasicAuth

from . import fanvil

app = Flask(__name__)

LDAP_SERVER = os.environ.get("DOORMAN_LDAP_SERVER", "localhost")
LDAP_USE_SSL = os.environ.get("DOORMAN_LDAP_USE_SSL", False)
LDAP_BASE_DN = os.environ.get(
    "DOORMAN_LDAP_BASE_DN", "cn=users,cn=accounts,dc=pawprint,dc=space"
)
LDAP_USER_DN = os.environ.get("DOORMAN_LDAP_USER_DN")
LDAP_PASS = os.environ.get("DOORMAN_LDAP_PASS")

FANVIL_SSL = os.environ.get("DOORMAN_FANVIL_SSL", False)
FANVIL_VERIFY_CA = os.environ.get("DOORMAN_FANVIL_CA")
FANVIL_USER = os.environ.get("DOORMAN_FANVIL_USER", "admin")
FANVIL_PASS = os.environ.get("DOORMAN_FANVIL_PASS", "admin")


def open_door(remote_addr):
    if FANVIL_SSL:
        protocol = "https"
    else:
        protocol = "http"

    try:
        reversed_dns = socket.gethostbyaddr(remote_addr)
        host = reversed_dns[0]
    except Exception:
        app.logger.warn("Unable to get reverse DNS for request host")
        host = remote_addr

    # Change code= to match the value in EGS settings > features > calling
    # password (* by default)
    url = f"{protocol}://{host}/cgi-bin/ConfigManApp.com?Key=F_LOCK&code=*"
    auth = HTTPBasicAuth(FANVIL_USER, FANVIL_PASS)

    requests.get(url, auth=auth, verify=FANVIL_VERIFY_CA)


def lookup_card(card_number):
    ldap_server = ldap3.Server(LDAP_SERVER, use_ssl=LDAP_USE_SSL)
    with ldap3.Connection(ldap_server, LDAP_USER_DN, LDAP_PASS, auto_bind=True) as conn:
        app.logger.info(conn)
        search_filter = f"(&(rfidbadge={card_number})(!(nsAccountLock=TRUE)))"
        app.logger.debug(f"SearchDN: {LDAP_BASE_DN} Search filter: {search_filter}")
        conn.search(
            LDAP_BASE_DN, search_filter, attributes=["cn"],
        )
        app.logger.info(f"Response: {conn.response}")

    if len(conn.response) == 1:
        app.logger.info(f"Card found: {conn.response[0]['attributes']}")
        open_thread = threading.Thread(target=open_door, args=(request.remote_addr,))
        open_thread.start()
    else:
        app.logger.info("Card not found")

    # TODO: write cn to audit log (influxdb)


@app.route("/", methods=["GET", "POST"])
def auth():
    if request.method == "POST":
        input_type, input_value = fanvil.parse_command(request.get_data())
        if input_type == fanvil.CARD_ID:
            app.logger.info(f"Got card: {input_value}")
            lookup_card(input_value)
        elif input_type == fanvil.KEYPAD_INPUT:
            app.logger.info(f"Got keypad input: {input_value}")
        return "Hello, post!"
    return "Nope"
