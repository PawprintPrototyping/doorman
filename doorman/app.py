#!/usr/bin/env python
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import apprise
import httpx
import ldap3
import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.responses import Response
from rich.logging import RichHandler

from . import fanvil
from .ipc import door_access_queue

logger = logging.getLogger("doorman")


def _init_sentry() -> None:
    """Initialize Sentry if DOORMAN_SENTRY_DSN is set; no-op otherwise.

    The FastAPI integration is auto-enabled when sentry-sdk sees fastapi
    installed, so we don't need to pass integrations= explicitly. This must
    run before `app = FastAPI(...)` is constructed.
    """
    dsn = os.environ.get("DOORMAN_SENTRY_DSN")
    if not dsn:
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("DOORMAN_SENTRY_ENVIRONMENT"),
        release=os.environ.get("DOORMAN_SENTRY_RELEASE"),
        traces_sample_rate=float(
            os.environ.get("DOORMAN_SENTRY_TRACES_SAMPLE_RATE", "0.1")
        ),
        send_default_pii=True,
        auto_session_tracking=False,  # GlitchTip does not support sessions
        # enable_logs=True,  # Opt-in: send logs to GlitchTip (uses disk space)
    )


_init_sentry()


def env_bool(s) -> bool:
    return str(s).lower() in ("1", "t", "true", "y", "yes")


LDAP_ENABLE = env_bool(os.environ.get("DOORMAN_LDAP_ENABLE", True))
MM_ENABLE = env_bool(os.environ.get("DOORMAN_MM_ENABLE", True))
MM_DATA_FILE = os.environ.get("DOORMAN_MM_DATA_FILE", "/tmp/mm-doorman.json")

LDAP_SERVER = os.environ.get("DOORMAN_LDAP_SERVER", "localhost")
LDAP_USE_SSL = env_bool(os.environ.get("DOORMAN_LDAP_USE_SSL", False))
LDAP_BASE_DN = os.environ.get(
    "DOORMAN_LDAP_BASE_DN", "cn=users,cn=accounts,dc=pawprint,dc=space"
)
LDAP_USER_DN = os.environ.get("DOORMAN_LDAP_USER_DN")
LDAP_PASS = os.environ.get("DOORMAN_LDAP_PASS")

FANVIL_URL = os.environ.get("DOORMAN_FANVIL_URL", "http://fanvil")
FANVIL_VERIFY_CA = os.environ.get("DOORMAN_FANVIL_CA")
FANVIL_USER = os.environ.get("DOORMAN_FANVIL_USER", "admin")
FANVIL_PASS = os.environ.get("DOORMAN_FANVIL_PASS", "admin")

DOORBELL_WEBHOOK = os.environ.get("DOORMAN_DOORBELL_WEBHOOK")
ACCESS_PINS = json.loads(os.environ.get("DOORMAN_ACCESS_PINS", "{}"))
SUCCESS_WEBHOOK = os.environ.get("DOORMAN_SUCCESS_WEBHOOK")
LDAP_APPRISE_URL = os.environ.get("DOORMAN_LDAP_APPRISE_URL")
DEBUG = env_bool(os.environ.get("DEBUG", False))

HTTP_TIMEOUT = httpx.Timeout(5.0)


LOG_JSON = env_bool(os.environ.get("DOORMAN_LOG_JSON", False))


def setup_logging() -> None:
    """Configure logging for doorman + doorman_client loggers.

    Uses structured JSON output when DOORMAN_LOG_JSON is set (production),
    otherwise uses Rich for human-friendly local development output.

    Called from the FastAPI lifespan and from websocket_client when run
    standalone, so logs appear regardless of which entrypoint is hosting us.
    """
    level = logging.DEBUG if DEBUG else logging.INFO

    if LOG_JSON:
        from pythonjsonlogger.json import JsonFormatter

        handler: logging.Handler = logging.StreamHandler()
        handler.setFormatter(
            JsonFormatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
                rename_fields={"asctime": "timestamp", "levelname": "level"},
            )
        )
    else:
        handler = RichHandler()
        handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))

    for name in ("doorman", "doorman_client"):
        log = logging.getLogger(name)
        if not log.handlers:
            log.addHandler(handler)
        log.setLevel(level)
        log.propagate = False


def xml_response(status: int) -> Response:
    body = f'<?xml version="1.0" encoding="UTF-8" ?><RetCode>{status}</RetCode>'
    return Response(
        content=body,
        media_type="application/xml; charset=utf-8",
        status_code=status,
    )


_background_tasks: set[asyncio.Task] = set()


def _fire_and_forget(coro) -> None:
    """Schedule a coroutine as a background task with error logging."""

    async def _wrapper():
        try:
            await coro
        except Exception as e:
            logger.error(f"Background task failed: {e}")
            sentry_sdk.capture_exception(e)

    task = asyncio.create_task(_wrapper())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()

    # Single shared HTTP client with explicit timeouts for outbound webhooks.
    app.state.http = httpx.AsyncClient(timeout=HTTP_TIMEOUT)

    import threading

    from websocket_client import MMAccessClient

    ws_client: dict = {}

    def start_websocket_client() -> None:
        try:
            client = MMAccessClient(debug=DEBUG)
            ws_client["instance"] = client
            client.run()
        except Exception as e:
            logger.exception("MemberMatters websocket client crashed")
            sentry_sdk.capture_exception(e)

    ws_thread = threading.Thread(
        target=start_websocket_client, daemon=True, name="mm-ws-client"
    )
    ws_thread.start()

    try:
        yield
    finally:
        # Close the websocket so run_forever() returns and the pump thread exits.
        client = ws_client.get("instance")
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.exception("Error closing websocket client")
        await app.state.http.aclose()


app = FastAPI(lifespan=lifespan)


@app.exception_handler(Exception)
async def xml_unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    """Fanvil expects XML for every response; never let a JSON 500 leak out."""
    logger.exception("Unhandled exception", exc_info=exc)
    sentry_sdk.capture_exception(exc)
    return xml_response(500)


def open_door() -> None:
    """Trigger the Fanvil door relay via its HTTP API.

    Synchronous on purpose: it is called from the websocket worker thread,
    which has no event loop. FastAPI handlers should call via asyncio.to_thread.
    """
    url = f"{FANVIL_URL}/cgi-bin/ConfigManApp.com?Key=F_LOCK&code=*"
    verify = FANVIL_VERIFY_CA if FANVIL_VERIFY_CA else False
    with httpx.Client(verify=verify, timeout=HTTP_TIMEOUT) as client:
        client.get(url, auth=(FANVIL_USER, FANVIL_PASS))


def _lookup_mm(card_number: str) -> bool:
    with open(MM_DATA_FILE) as f:
        mm_data = json.load(f)
    authorized_tags = mm_data.get("tags", [])
    logger.debug(
        f"Loaded {len(authorized_tags)} tags from MemberMatters cache at {MM_DATA_FILE}"
    )
    locked_out = mm_data.get("locked_out", False)
    if locked_out:
        logger.info("This MemberMatters device was set to locked_out by the server!")
        return False

    if card_number in authorized_tags:
        logger.info(f"card_number: {card_number} is authorized by MemberMatters")
        return True

    logger.debug(f"card_number: {card_number} not authorized by MemberMatters")
    return False


def _notify_ldap(card_number: str, attributes: dict) -> None:
    """Send a notification when access is granted via LDAP."""
    if not LDAP_APPRISE_URL:
        return
    ap = apprise.Apprise()
    ap.add(LDAP_APPRISE_URL)
    cn = attributes.get("cn", "unknown")
    uid = attributes.get("uid", "unknown")
    ap.notify(
        title="Doorman",
        body=(
            f"Card {card_number} was granted access via LDAP.\n" f"CN: {cn}, UID: {uid}"
        ),
    )


def _ldap_search(card_number: str) -> list:
    """Synchronous LDAP search; call via asyncio.to_thread from async code."""
    ldap_server = ldap3.Server(LDAP_SERVER, use_ssl=LDAP_USE_SSL)
    with ldap3.Connection(ldap_server, LDAP_USER_DN, LDAP_PASS, auto_bind=True) as conn:
        logger.info(str(conn))
        search_filter = f"(&(rfidbadge={card_number})(!(nsAccountLock=TRUE)))"
        logger.debug(f"SearchDN: {LDAP_BASE_DN} Search filter: {search_filter}")
        conn.search(
            LDAP_BASE_DN,
            search_filter,
            attributes=["cn", "uid"],
        )
        logger.info(f"Response: {conn.response}")
        return list(conn.response)


async def _lookup_ldap(request: Request, card_number: str) -> bool:
    response = await asyncio.to_thread(_ldap_search, card_number)
    if len(response) != 1:
        logger.info("Card not found")
        return False
    attributes = response[0]["attributes"]
    logger.info(f"Card found: {attributes}")
    if LDAP_APPRISE_URL:
        _fire_and_forget(asyncio.to_thread(_notify_ldap, card_number, attributes))
    if SUCCESS_WEBHOOK:
        webhook_data = {"_type": "CARD", "card_number": card_number}
        webhook_data.update(attributes)
        _fire_and_forget(
            request.app.state.http.post(SUCCESS_WEBHOOK, data=webhook_data)
        )
    return True


async def lookup_card(request: Request, card_number: str) -> bool:
    mm_authorized = await asyncio.to_thread(_lookup_mm, card_number)
    if mm_authorized:
        # Only notify MemberMatters of access if the card was in its own tag list
        door_access_queue.put(
            {
                "id_number": card_number,
                "success": True,
                "method": "rfid",
            }
        )
        if SUCCESS_WEBHOOK:
            webhook_data = {"_type": "CARD", "card_number": card_number}
            _fire_and_forget(
                request.app.state.http.post(SUCCESS_WEBHOOK, data=webhook_data)
            )
        return True
    return await _lookup_ldap(request, card_number)


async def lookup_pin(request: Request, input_value: str) -> bool:
    for pin in ACCESS_PINS:
        if input_value == str(pin):
            logger.info(f"Access granted by PIN for {ACCESS_PINS[pin]}")
            if SUCCESS_WEBHOOK:
                webhook_data = {"_type": "PIN", "cn": ACCESS_PINS[pin]}
                _fire_and_forget(
                    request.app.state.http.post(SUCCESS_WEBHOOK, data=webhook_data)
                )
            return True
    return False


@app.api_route("/", methods=["GET", "POST"])
async def auth(request: Request) -> Response:
    if request.method == "POST":
        body = await request.body()
        try:
            input_type, input_value = fanvil.parse_command(body)
        except fanvil.FanvilParseError as e:
            logger.warning(f"Failed to parse Fanvil command: {e}")
            return xml_response(401)
        success = False
        if input_type == fanvil.CARD_ID:
            logger.info(f"Got card: {input_value}")
            success = await lookup_card(request, input_value)
        elif input_type == fanvil.KEYPAD_INPUT:
            logger.info(f"Got keypad input: {input_value}")
            success = await lookup_pin(request, input_value)
        if success:
            return xml_response(200)
    return xml_response(401)


@app.get("/fanvil/doorbell")
async def doorbell(request: Request) -> Response:
    """Historical function to work around webhook limitations in Home Assistant."""
    if not DOORBELL_WEBHOOK:
        return xml_response(200)
    try:
        await request.app.state.http.post(DOORBELL_WEBHOOK)
    except httpx.HTTPError as e:
        logger.error("Exception while sending doorbell webhook:")
        logger.exception(e)
        sentry_sdk.capture_exception(e)
    return xml_response(200)
