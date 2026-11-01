import unittest

from doorman import fanvil


class ParseUriTests(unittest.TestCase):
    def test_removes_fanvil_suffix(self) -> None:
        self.assertEqual(
            fanvil.parse_uri("doorOpen:Card_Id=3980476606@1"),
            ("doorOpen", fanvil.CARD_ID, "3980476606"),
        )


class ParseCommandTests(unittest.TestCase):
    def test_parses_card_id(self) -> None:
        payload = b"""<PhoneExecute>
            <ExecuteItem>URI="doorOpen:Card_Id=3980476606@1"</ExecuteItem>
        </PhoneExecute>"""

        self.assertEqual(
            fanvil.parse_command(payload),
            (fanvil.CARD_ID, "3980476606"),
        )

    def test_parses_keypad_input(self) -> None:
        payload = b"""<PhoneExecute>
            <ExecuteItem>URI="doorOpen:Keypad_Input=2468@1"</ExecuteItem>
        </PhoneExecute>"""

        self.assertEqual(
            fanvil.parse_command(payload),
            (fanvil.KEYPAD_INPUT, "2468"),
        )

    def test_rejects_missing_execute_item(self) -> None:
        with self.assertRaises(fanvil.FanvilParseError):
            fanvil.parse_command(b"<PhoneExecute />")

    def test_rejects_empty_execute_item(self) -> None:
        with self.assertRaises(fanvil.FanvilParseError):
            fanvil.parse_command(b"<PhoneExecute><ExecuteItem /></PhoneExecute>")

    def test_normalizes_malformed_uri_raises_fanvil_parse_error(self) -> None:
        payload = b"""<PhoneExecute>
            <ExecuteItem>URI="not-a-command"</ExecuteItem>
        </PhoneExecute>"""

        with self.assertRaises(fanvil.FanvilParseError):
            fanvil.parse_command(payload)

    def test_rejects_unsupported_command(self) -> None:
        payload = b"""<PhoneExecute>
            <ExecuteItem>URI="doorClose:Card_Id=3980476606"</ExecuteItem>
        </PhoneExecute>"""

        with self.assertRaises(fanvil.CommandParseError):
            fanvil.parse_command(payload)

    def test_rejects_unsupported_key(self) -> None:
        payload = b"""<PhoneExecute>
            <ExecuteItem>URI="doorOpen:Unsupported=3980476606"</ExecuteItem>
        </PhoneExecute>"""

        with self.assertRaises(fanvil.CommandParseError):
            fanvil.parse_command(payload)
