"""Fan entities for the Govee Life integration."""

from __future__ import annotations
from typing import Final
import logging
import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.fan import (
    FanEntity,
    FanEntityFeature,
)
from homeassistant.const import (
    CONF_DEVICES,
    STATE_ON,
    STATE_OFF,
    STATE_UNKNOWN,
    PERCENTAGE,
)

from .entities import GoveeLifePlatformEntity
from .const import DOMAIN, CONF_COORDINATORS
from .utils import GoveeAPI_GetCachedStateValue, async_GoveeAPI_ControlDevice

_LOGGER: Final = logging.getLogger(__name__)
PLATFORM = 'fan'
PLATFORM_DEVICE_TYPES = [
    'devices.types.air_purifier',
    'devices.types.fan'
]

MODE_ICONS = {
    'Low': 'mdi:fan-speed-1',
    'Medium': 'mdi:fan-speed-2',
    'High': 'mdi:fan-speed-3',
    'Sleep': 'mdi:power-sleep',
    'Auto': 'mdi:autorenew',
    'Custom': 'mdi:cog-outline'
}

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up fan platform."""
    entities = []

    try:
        entry_data = hass.data[DOMAIN][entry.entry_id]
        api_devices = entry_data[CONF_DEVICES]
    except Exception as e:
        _LOGGER.error("%s - async_setup_entry %s: Failed to get devices: %s (%s.%s)",
                     entry.entry_id, PLATFORM, str(e), e.__class__.__module__, type(e).__name__)
        return

    for device_cfg in api_devices:
        try:
            if device_cfg.get('type') not in PLATFORM_DEVICE_TYPES:
                continue

            device = device_cfg.get('device')
            coordinator = entry_data[CONF_COORDINATORS][device]
            entity = GoveeLifeFan(hass, entry, coordinator, device_cfg, platform=PLATFORM)
            entities.append(entity)
            await asyncio.sleep(0)
        except Exception as e:
            _LOGGER.error("%s - async_setup_entry %s: Setup failed: %s (%s.%s)",
                         entry.entry_id, PLATFORM, str(e), e.__class__.__module__, type(e).__name__)

    if entities:
        async_add_entities(entities)

class GoveeLifeFan(FanEntity, GoveeLifePlatformEntity):
    """Fan class for Govee Life integration."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator, device_cfg, **kwargs):
        """Initialize the fan entity."""
        self._attr_preset_modes = []
        self._preset_mode = None
        self._attr_supported_features = FanEntityFeature.PRESET_MODE
        self._state_mapping = {}
        self._state_mapping_set = {}
        self._modes_mapping = {}
        
        # Keep original device name
        self._attr_name = device_cfg.get('deviceName')
        self._attr_unique_id = f"{device_cfg.get('device')}_fan"
        
        super().__init__(hass, entry, coordinator, device_cfg, **kwargs)

    def _init_platform_specific(self, **kwargs):
        """Initialize platform-specific capabilities."""
        capabilities = self._device_cfg.get('capabilities', [])

        for cap in capabilities:
            try:
                if cap['type'] == 'devices.capabilities.on_off':
                    self._handle_power_capability(cap)
                elif cap['type'] == 'devices.capabilities.work_mode':
                    self._handle_work_mode_capability(cap)
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

    def _handle_work_mode_capability(self, cap):
        """Handle work mode capability."""
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
                                    if base_name not in self._attr_preset_modes:
                                        self._attr_preset_modes.append(base_name)
                                    
                                    self._modes_mapping[base_name] = {
                                        'workMode': mode['value'],
                                        'modeValue': gear['value']
                                    }
                        else:
                            # Handle other modes
                            base_name = mode['name']
                            if base_name not in self._attr_preset_modes:
                                self._attr_preset_modes.append(base_name)
                            self._modes_mapping[base_name] = {
                                'workMode': mode['value'],
                                'modeValue': 0,
                            }

    @property
    def name(self) -> str:
        """Return the display name of this fan."""
        return self._attr_name

    @property
    def preset_modes(self) -> list[str] | None:
        """Return the list of available preset modes."""
        return self._attr_preset_modes

    def option_icon(self, option: str) -> str | None:
        """Return the icon for the provided option."""
        return MODE_ICONS.get(option)

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
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

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if preset_mode not in self._modes_mapping:
            _LOGGER.error("%s - Invalid preset mode: %s", self._identifier, preset_mode)
            return

        mode_config = self._modes_mapping[preset_mode]
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
    def extra_state_attributes(self):
        """Return entity specific state attributes."""
        attributes = {}
        
        # Add filter life attribute
        filter_life = GoveeAPI_GetCachedStateValue(
            self.hass,
            self._entry_id,
            self._device_cfg['device'],
            'devices.capabilities.property',
            'filterLifeTime'
        )
        if filter_life is not None:
            attributes['filter_life'] = f"{filter_life}{PERCENTAGE}"

        # Add air quality attribute
        air_quality = GoveeAPI_GetCachedStateValue(
            self.hass,
            self._entry_id,
            self._device_cfg['device'],
            'devices.capabilities.property',
            'airQuality'
        )
        if air_quality is not None:
            attributes['air_quality'] = air_quality

        return attributes

    @property
    def is_on(self) -> bool:
        """Return true if fan is on."""
        value = GoveeAPI_GetCachedStateValue(
            self.hass, self._entry_id, self._device_cfg['device'],
            'devices.capabilities.on_off', 'powerSwitch'
        )
        return self._state_mapping.get(value) == STATE_ON

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the fan on."""
        capability = {
            "type": "devices.capabilities.on_off",
            "instance": "powerSwitch",
            "value": self._state_mapping_set[STATE_ON]
        }
        
        if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, capability):
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the fan off."""
        capability = {
            "type": "devices.capabilities.on_off",
            "instance": "powerSwitch",
            "value": self._state_mapping_set[STATE_OFF]
        }
        
        if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, capability):
            self.async_write_ha_state()
