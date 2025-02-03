"""Humidifier entities for the Govee Life integration."""

from __future__ import annotations
from typing import Final
import logging
import asyncio

from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.humidifier import (
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.const import (
    CONF_DEVICES,
    STATE_ON,
    STATE_OFF,
    STATE_UNKNOWN,
)

from .entities import GoveeLifePlatformEntity
from .const import DOMAIN, CONF_COORDINATORS
from .utils import GoveeAPI_GetCachedStateValue, async_GoveeAPI_ControlDevice

_LOGGER: Final = logging.getLogger(__name__)
PLATFORM = 'humidifier'
PLATFORM_DEVICE_TYPES = [
    'devices.types.humidifier',
    'devices.types.dehumidifier'
]

MODE_ICONS = {
    'Low': 'mdi:fan-speed-1',
    'Medium': 'mdi:fan-speed-2',
    'High': 'mdi:fan-speed-3',
    'Sleep': 'mdi:power-sleep',
    'Auto': 'mdi:autorenew',
    'Dryer': 'mdi:tumble-dryer',
    'Custom': 'mdi:cog-outline'
}

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up humidifier platform with advanced Govee API support."""
    entities = []

    try:
        entry_data = hass.data[DOMAIN][entry.entry_id]
        api_devices = entry_data[CONF_DEVICES]
    except Exception as e:
        _LOGGER.error("%s - async_setup_entry %s: Failed to get devices: %s (%s.%s)",
                     entry.entry_id, PLATFORM, str(e), e.__class__.__module__, type(e).__name__)
        return

    for device_cfg in api_devices:
        if device_cfg.get('type') not in PLATFORM_DEVICE_TYPES:
            continue

        device = device_cfg.get('device')
        coordinator = entry_data[CONF_COORDINATORS][device]
        entity = GoveeLifeHumidifier(hass, entry, coordinator, device_cfg, platform=PLATFORM)
        entities.append(entity)
        await asyncio.sleep(0)

    if entities:
        async_add_entities(entities)

class GoveeLifeHumidifier(HumidifierEntity, GoveeLifePlatformEntity):
    """Humidifier class for Govee Life integration."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator, device_cfg, **kwargs):
        """Initialize the humidifier entity."""
        self._attr_available_modes = []
        self._attr_mode = None
        self._attr_supported_features = HumidifierEntityFeature.MODES
        self._attr_min_humidity = 30
        self._attr_max_humidity = 80
        self._attr_target_humidity = None
        self._attr_current_humidity = None
        self._attr_extra_state_attributes = {}
        self._state_mapping = {}
        self._state_mapping_set = {}
        self._modes_mapping = {}
        
        super().__init__(hass, entry, coordinator, device_cfg, **kwargs)

        # Set up event listener
        hass.bus.async_listen(f"{DOMAIN}_event", self._handle_event)

    @callback
    def _handle_event(self, event):
        """Handle device events."""
        if event.data.get('device') != self._device_cfg.get('device'):
            return
            
        try:
            if 'waterFullEvent' in event.data:
                is_full = bool(event.data['waterFullEvent'])
                self._attr_extra_state_attributes['water_full'] = is_full
                if is_full:
                    _LOGGER.warning("%s - Water tank is full", self._identifier)
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("%s - Failed to handle event: %s", self._identifier, str(e))

    def _init_platform_specific(self, **kwargs):
        """Initialize platform-specific capabilities."""
        capabilities = self._device_cfg.get('capabilities', [])
        
        # Set device class based on type
        self._attr_device_class = (
            HumidifierDeviceClass.DEHUMIDIFIER if 
            'dehumidifier' in self._device_cfg.get('type', '') else
            HumidifierDeviceClass.HUMIDIFIER
        )

        for cap in capabilities:
            try:
                if cap['type'] == 'devices.capabilities.on_off':
                    self._handle_power_capability(cap)
                elif cap['type'] == 'devices.capabilities.range':
                    self._handle_range_capability(cap)
                elif cap['type'] == 'devices.capabilities.work_mode':
                    self._handle_work_mode_capability(cap)
                elif cap['type'] == 'devices.capabilities.event':
                    self._handle_event_capability(cap)
            except Exception as e:
                _LOGGER.warning("%s - Capability init failed: %s (%s)", self._identifier, str(e), cap)

    def _handle_power_capability(self, cap):
        """Handle power control capability."""
        for option in cap['parameters']['options']:
            if option['name'] == 'on':
                self._state_mapping[option['value']] = STATE_ON
                self._state_mapping_set[STATE_ON] = option['value']
            elif option['name'] == 'off':
                self._state_mapping[option['value']] = STATE_OFF
                self._state_mapping_set[STATE_OFF] = option['value']

    def _handle_range_capability(self, cap):
        """Handle humidity range capability."""
        if cap['instance'] == 'humidity':
            self._attr_min_humidity = cap['parameters']['range']['min']
            self._attr_max_humidity = cap['parameters']['range']['max']

    def _handle_work_mode_capability(self, cap):
        """Handle advanced work mode configurations."""
        if cap['instance'] == 'workMode':
            # Process work mode fields
            for field in cap['parameters']['fields']:
                if field['fieldName'] == 'workMode':
                    for mode in field['options']:
                        if mode['name'] == 'gearMode':
                            # Handle gear modes
                            mode_options = next(
                                (f['options'] for f in cap['parameters']['fields'] 
                                if f['fieldName'] == 'modeValue' 
                                and 'gearMode' in str(f)), []
                            )
                            if mode_options:
                                for gear in mode_options[0].get('options', []):
                                    base_name = gear['name']
                                    if base_name not in self._attr_available_modes:
                                        self._attr_available_modes.append(base_name)
                                    
                                    self._modes_mapping[base_name] = {
                                        'workMode': mode['value'],
                                        'modeValue': gear['value'],
                                    }
                        else:
                            # Handle other modes
                            base_name = mode['name']
                            if base_name not in self._attr_available_modes:
                                self._attr_available_modes.append(base_name)
                            self._modes_mapping[base_name] = {
                                'workMode': mode['value'],
                                'modeValue': 0,
                            }

    def _handle_event_capability(self, cap):
        """Handle water full events."""
        if cap['instance'] == 'waterFullEvent':
            self._attr_extra_state_attributes['water_full'] = False
            if 'eventState' in cap and 'options' in cap['eventState']:
                for option in cap['eventState']['options']:
                    if option['name'] == 'waterFull':
                        self._attr_extra_state_attributes['water_full_message'] = option['message']

    @property
    def available_modes(self) -> list[str] | None:
        """Return the list of available modes."""
        return self._attr_available_modes

    @property
    def mode(self) -> str | None:
        """Return current mode."""
        work_mode = GoveeAPI_GetCachedStateValue(
            self.hass, self._entry_id, self._device_cfg['device'],
            'devices.capabilities.work_mode', 'workMode'
        )
        if not work_mode:
            return None

        # Find mode name from mapping
        for mode_name, values in self._modes_mapping.items():
            if (values['workMode'] == work_mode.get('workMode') and 
                values['modeValue'] == work_mode.get('modeValue', 0)):
                return mode_name
        return None

    def option_icon(self, option: str) -> str | None:
        """Return the icon for the provided option."""
        return MODE_ICONS.get(option)

    async def async_set_mode(self, mode: str) -> None:
        """Set new mode."""
        if mode not in self._modes_mapping:
            _LOGGER.error("%s - Invalid mode: %s", self._identifier, mode)
            return

        mode_config = self._modes_mapping[mode]
        capability = {
            "type": "devices.capabilities.work_mode",
            "instance": "workMode",
            "value": {
                "workMode": mode_config['workMode'],
                "modeValue": mode_config['modeValue']
            }
        }

        if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, capability):
            self.async_write_ha_state()

    @property
    def target_humidity(self) -> int | None:
        """Return target humidity setting."""
        return GoveeAPI_GetCachedStateValue(
            self.hass, self._entry_id, self._device_cfg['device'],
            'devices.capabilities.range', 'humidity'
        )

    async def async_set_humidity(self, humidity: int) -> None:
        """Set new target humidity."""
        if humidity < self._attr_min_humidity or humidity > self._attr_max_humidity:
            _LOGGER.warning("%s - Invalid humidity value: %s", self._identifier, humidity)
            return

        capability = {
            "type": "devices.capabilities.range",
            "instance": "humidity",
            "value": humidity
        }
        
        if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, capability):
            self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return true if humidifier is on."""
        value = GoveeAPI_GetCachedStateValue(
            self.hass, self._entry_id, self._device_cfg['device'],
            'devices.capabilities.on_off', 'powerSwitch'
        )
        return self._state_mapping.get(value) == STATE_ON

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the humidifier on."""
        capability = {
            "type": "devices.capabilities.on_off",
            "instance": "powerSwitch",
            "value": self._state_mapping_set[STATE_ON]
        }
        
        if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, capability):
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the humidifier off."""
        capability = {
            "type": "devices.capabilities.on_off",
            "instance": "powerSwitch",
            "value": self._state_mapping_set[STATE_OFF]
        }
        
        if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, capability):
            self.async_write_ha_state()
