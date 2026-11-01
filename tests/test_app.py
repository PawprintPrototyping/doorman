import json
import unittest
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import httpx

from tests.support import FakeHttpClient, FakeRequest, app


class EnvironmentAndResponseTests(unittest.TestCase):
    def test_env_bool_accepts_truthy_values(self):
        for value in ("1", "t", "TRUE", "Y", "yes"):
            with self.subTest(value=value):
                self.assertTrue(app.env_bool(value))

    def test_env_bool_rejects_false_like_values(self):
        for value in (None, "", "0", "false", "no", "unexpected"):
            with self.subTest(value=value):
                self.assertFalse(app.env_bool(value))

    def test_xml_response_contains_status_and_xml_media_type(self):
        response = app.xml_response(401)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.body,
            b'<?xml version="1.0" encoding="UTF-8" ?><RetCode>401</RetCode>',
        )
        self.assertEqual(response.media_type, "application/xml; charset=utf-8")


class MemberMattersLookupTests(unittest.TestCase):
    def test_lookup_mm_authorizes_known_card(self):
        cache = json.dumps({"tags": ["card-123"]})

        with patch("builtins.open", mock_open(read_data=cache)):
            self.assertTrue(app._lookup_mm("card-123"))

    def test_lookup_mm_rejects_unknown_card(self):
        cache = json.dumps({"tags": ["card-123"]})

        with patch("builtins.open", mock_open(read_data=cache)):
            self.assertFalse(app._lookup_mm("card-456"))

    def test_lookup_mm_rejects_access_while_locked_out(self):
        cache = json.dumps({"tags": ["card-123"], "locked_out": True})

        with patch("builtins.open", mock_open(read_data=cache)):
            self.assertFalse(app._lookup_mm("card-123"))


class LdapLookupTests(unittest.IsolatedAsyncioTestCase):
    def test_ldap_search_builds_expected_request(self):
        response = [{"attributes": {"cn": "Ada", "uid": "ada"}}]
        with (
            patch.object(app.ldap3, "Server") as server,
            patch.object(app.ldap3, "Connection") as connection,
        ):
            ldap_connection = connection.return_value.__enter__.return_value
            ldap_connection.response = response

            result = app._ldap_search("card-123")

        self.assertEqual(result, response)
        server.assert_called_once_with(app.LDAP_SERVER, use_ssl=app.LDAP_USE_SSL)
        connection.assert_called_once_with(
            server.return_value,
            app.LDAP_USER_DN,
            app.LDAP_PASS,
            auto_bind=True,
        )
        ldap_connection.search.assert_called_once_with(
            app.LDAP_BASE_DN,
            "(&(rfidbadge=card-123)(!(nsAccountLock=TRUE)))",
            attributes=["cn", "uid"],
        )

    async def test_lookup_ldap_rejects_no_results(self):
        request = FakeRequest()
        with patch.object(
            app.asyncio, "to_thread", new=AsyncMock(return_value=[])
        ) as to_thread:
            authorized = await app._lookup_ldap(request, "card-123")

        self.assertFalse(authorized)
        to_thread.assert_awaited_once_with(app._ldap_search, "card-123")

    async def test_lookup_ldap_rejects_multiple_results(self):
        request = FakeRequest()
        response = [{"attributes": {}}, {"attributes": {}}]
        with patch.object(
            app.asyncio, "to_thread", new=AsyncMock(return_value=response)
        ):
            authorized = await app._lookup_ldap(request, "card-123")

        self.assertFalse(authorized)

    async def test_lookup_ldap_authorizes_exactly_one_result(self):
        request = FakeRequest()
        response = [{"attributes": {"cn": "Ada", "uid": "ada"}}]
        with patch.object(
            app.asyncio, "to_thread", new=AsyncMock(return_value=response)
        ):
            authorized = await app._lookup_ldap(request, "card-123")

        self.assertTrue(authorized)


class CardAndPinLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_lookup_card_queues_membermatters_authorization(self):
        request = FakeRequest()
        access_queue = MagicMock()
        with (
            patch.object(app, "_lookup_mm", return_value=True) as lookup_mm,
            patch.object(
                app.asyncio, "to_thread", new=AsyncMock(return_value=True)
            ) as to_thread,
            patch.object(app, "door_access_queue", access_queue),
            patch.object(app, "_lookup_ldap", new=AsyncMock()) as lookup_ldap,
        ):
            authorized = await app.lookup_card(request, "card-123")

        self.assertTrue(authorized)
        to_thread.assert_awaited_once_with(lookup_mm, "card-123")
        access_queue.put.assert_called_once_with(
            {"id_number": "card-123", "success": True}
        )
        lookup_ldap.assert_not_awaited()

    async def test_lookup_card_falls_back_to_ldap(self):
        request = FakeRequest()
        access_queue = MagicMock()
        with (
            patch.object(app, "_lookup_mm", return_value=False),
            patch.object(app.asyncio, "to_thread", new=AsyncMock(return_value=False)),
            patch.object(app, "door_access_queue", access_queue),
            patch.object(
                app, "_lookup_ldap", new=AsyncMock(return_value=True)
            ) as lookup_ldap,
        ):
            authorized = await app.lookup_card(request, "card-123")

        self.assertTrue(authorized)
        access_queue.put.assert_not_called()
        lookup_ldap.assert_awaited_once_with(request, "card-123")

    async def test_lookup_pin_authorizes_matching_pin(self):
        request = FakeRequest()
        with patch.object(app, "ACCESS_PINS", {1234: "Ada"}):
            authorized = await app.lookup_pin(request, "1234")

        self.assertTrue(authorized)

    async def test_lookup_pin_rejects_nonmatching_pin(self):
        request = FakeRequest()
        with patch.object(app, "ACCESS_PINS", {1234: "Ada"}):
            authorized = await app.lookup_pin(request, "9999")

        self.assertFalse(authorized)


class AuthenticationRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_rejects_get_request(self):
        response = await app.auth(FakeRequest(method="GET"))

        self.assertEqual(response.status_code, 401)

    async def test_auth_rejects_invalid_fanvil_command(self):
        request = FakeRequest(body=b"invalid")
        with patch.object(
            app.fanvil,
            "parse_command",
            side_effect=app.fanvil.FanvilParseError("invalid"),
        ):
            response = await app.auth(request)

        self.assertEqual(response.status_code, 401)

    async def test_auth_dispatches_card_to_card_lookup(self):
        request = FakeRequest(body=b"card")
        with (
            patch.object(
                app.fanvil,
                "parse_command",
                return_value=(app.fanvil.CARD_ID, "card-123"),
            ),
            patch.object(
                app, "lookup_card", new=AsyncMock(return_value=True)
            ) as lookup_card,
        ):
            response = await app.auth(request)

        self.assertEqual(response.status_code, 200)
        lookup_card.assert_awaited_once_with(request, "card-123")

    async def test_auth_dispatches_keypad_to_pin_lookup(self):
        request = FakeRequest(body=b"pin")
        with (
            patch.object(
                app.fanvil,
                "parse_command",
                return_value=(app.fanvil.KEYPAD_INPUT, "2468"),
            ),
            patch.object(
                app, "lookup_pin", new=AsyncMock(return_value=False)
            ) as lookup_pin,
        ):
            response = await app.auth(request)

        self.assertEqual(response.status_code, 401)
        lookup_pin.assert_awaited_once_with(request, "2468")


class DoorAndDoorbellTests(unittest.IsolatedAsyncioTestCase):
    def test_open_door_uses_configured_http_arguments(self):
        with (
            patch.object(app, "FANVIL_URL", "https://fanvil.unit-test.invalid"),
            patch.object(app, "FANVIL_VERIFY_CA", "/tmp/fanvil-ca.pem"),
            patch.object(app, "FANVIL_USER", "door-user"),
            patch.object(app, "FANVIL_PASS", "door-password"),
            patch.object(app.httpx, "Client") as http_client,
        ):
            app.open_door()

        http_client.assert_called_once_with(
            verify="/tmp/fanvil-ca.pem", timeout=app.HTTP_TIMEOUT
        )
        http_client.return_value.__enter__.return_value.get.assert_called_once_with(
            "https://fanvil.unit-test.invalid/cgi-bin/ConfigManApp.com?Key=F_LOCK&code=*",
            auth=("door-user", "door-password"),
        )

    async def test_doorbell_skips_request_when_webhook_is_unset(self):
        http = FakeHttpClient()
        request = FakeRequest(http=http)
        with patch.object(app, "DOORBELL_WEBHOOK", None):
            response = await app.doorbell(request)

        self.assertEqual(response.status_code, 200)
        http.post.assert_not_awaited()

    async def test_doorbell_posts_configured_webhook(self):
        http = FakeHttpClient()
        request = FakeRequest(http=http)
        with patch.object(app, "DOORBELL_WEBHOOK", "https://webhook.unit-test.invalid"):
            response = await app.doorbell(request)

        self.assertEqual(response.status_code, 200)
        http.post.assert_awaited_once_with("https://webhook.unit-test.invalid")

    async def test_doorbell_reports_http_errors_and_returns_success_xml(self):
        error = httpx.HTTPError("webhook failed")
        http = FakeHttpClient()
        http.post.side_effect = error
        request = FakeRequest(http=http)
        with (
            patch.object(app, "DOORBELL_WEBHOOK", "https://webhook.unit-test.invalid"),
            patch.object(app.sentry_sdk, "capture_exception") as capture_exception,
        ):
            response = await app.doorbell(request)

        self.assertEqual(response.status_code, 200)
        capture_exception.assert_called_once_with(error)
