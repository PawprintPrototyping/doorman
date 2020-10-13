import xml.etree.ElementTree as ET

CARD_ID = "Card_Id"
KEYPAD_INPUT = "Keypad_Input"

VALID_COMMANDS = ("doorOpen",)
VALID_URI_KEYS = (
    CARD_ID,
    KEYPAD_INPUT,
)


class BaseException(Exception):
    pass


class FanvilParseError(BaseException):
    pass


class CommandParseError(FanvilParseError):
    pass


def parse_uri(uri):
    command, attr = uri.split(":")
    key, val = attr.split("=")
    return command, key, val.rstrip("@1")


def parse_command(xml):
    """
    Example card read POST payload:

    <?xml version="1.0" encoding="UTF-8" ?>
    <PhoneExecute>
      <ExecuteItem>URI="doorOpen:Card_Id=3980476606@1"</ExecuteItem>
    <Info>
    <Model>i31S</Model>
    <MAC>0c:38:3e:20:cf:81</MAC>
    <Description>i31S IP Door Phone</Description>
    </Info>
    </PhoneExecute>
    """
    root = ET.fromstring(xml)
    ei = root.find("ExecuteItem")
    uri = ei.text.split('"')[1]

    try:
        command, key, val = parse_uri(uri)
    except (ValueError, KeyError) as e:
        raise FanvilParseError(e)

    try:
        assert command in VALID_COMMANDS
        assert key in VALID_URI_KEYS
    except AssertionError:
        raise CommandParseError(f"Invalid URI format: {uri}")

    return key, val
