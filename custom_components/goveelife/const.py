"""Constants for Govee Life."""

from __future__ import annotations
from typing import Final
from homeassistant.components.humidifier import HumidifierEntityFeature

DOMAIN: Final = 'goveelife'
FUNC_OPTION_UPDATES: Final = 'options_update_listener'
SUPPORTED_PLATFORMS: Final = [ "climate","switch","light","fan","sensor", "humidifier" ]
STATE_DEBUG_FILENAME: Final = '/_diagnostics.json'


DEFAULT_TIMEOUT: Final = 10
DEFAULT_POLL_INTERVAL: Final = 60
DEFAULT_NAME: Final = 'GoveeLife'
EVENT_PROPS_ID: Final = DOMAIN + '_property_message'

CONF_COORDINATORS: Final = 'coordinators'
CONF_API_COUNT: Final = 'api_count'
CONF_ENTRY_ID: Final = 'entry_id'

CLOUD_API_URL_DEVELOPER: Final = 'https://developer-api.govee.com/v1/appliance/devices/'
CLOUD_API_URL_OPENAPI: Final = 'https://openapi.api.govee.com/router/api/v1'
CLOUD_API_HEADER_KEY: Final = 'Govee-API-Key'

CONF_SEGMENT = "segment"
CONF_DEVICE_ID = "device_id"
CONF_ENTITY_ID = "entity_id"
CONF_MODE = "mode"
CONF_NAME = "name"
CONF_SENSITIVITY = "sensitivity"
CONF_AUTO_COLOR = "auto_color"
CONF_EFFECT = "effect"
CONF_SPEED = "speed"
CONF_BRIGHTNESS = "brightness"
CONF_COLOR = "color"
