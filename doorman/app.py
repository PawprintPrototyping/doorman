#!/usr/bin/env python
import os

import ldap3
import requests
from flask import Flask, request

from . import fanvil

app = Flask(__name__)

LDAP_SERVER = os.environ.get("DOORMAN_LDAP_SERVER", "localhost")
LDAP_USE_SSL = os.environ.get("DOORMAN_LDAP_USE_SSL", False)
LDAP_BASE_DN = os.environ.get(
    "DOORMAN_LDAP_BASE_DN", "cn=users,cn=accounts,dc=pawprint,dc=space"
)
LDAP_SEARCH_DN = os.environ.get(
    "DOORMAN_LDAP_SEARCH_DN", "cn=users,cn=accounts,cn=etc,dc=pawprint,dc=space",
)
LDAP_USER_DN = os.environ.get("DOORMAN_LDAP_USER_DN")
LDAP_PASS = os.environ.get("DOORMAN_LDAP_PASS")

RETURN_SSL = os.environ.get("DOORMAN_RETURN_SSL", False)


def open_door(request):
    if RETURN_SSL:
        protocol = "https"
    else:
        protocol = "http"


def lookup_card(card_number):
    ldap_server = ldap3.Server(LDAP_SERVER, use_ssl=LDAP_USE_SSL)
    with ldap3.Connection(ldap_server, LDAP_USER_DN, LDAP_PASS, auto_bind=True) as conn:
        app.logger.info(conn)
        # search_filter = f"(&(rfidbadge={card_number})(!(nsAccountLock=TRUE)))"
        search_filter = f"(rfidbadge={card_number})"
        app.logger.debug(f"SearchDN: {LDAP_SEARCH_DN} Search filter: {search_filter}")
        conn.search(
            LDAP_SEARCH_DN, search_filter, attributes=["cn",],
        )
        entries = conn.entries
        app.logger.info(f"Response: {conn.response}")

    if len(entries) == 1:
        app.logger.info("Card found: {entries[0]}")
    else:
        app.logger.info("Card not found")


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
