"""Humidifier entities for the Govee Life integration."""

from __future__ import annotations
from typing import Final, Any
import logging
import asyncio
from functools import partial

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_platform
from homeassistant.components.humidifier import (
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.const import (
    CONF_DEVICES,
    STATE_ON,
    STATE_OFF,
    PERCENTAGE,
    ATTR_MODE,
)
from homeassistant.exceptions import HomeAssistantError

from .entities import GoveeLifePlatformEntity
from .const import (
    DOMAIN,
    CONF_COORDINATORS,
    CONF_API_CLIENT,
    SERVICE_WATER_FULL_RESET
)
from .utils import (
    GoveeAPI_GetCachedStateValue,
    async_GoveeAPI_ControlDevice,
    govee_error_retry,
)

_LOGGER: Final = logging.getLogger(__name__)
platform = 'humidifier'
platform_device_types = [
    'devices.types.humidifier',
    'devices.types.dehumidifier'
]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up humidifier platform with services."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    api = entry_data[CONF_API_CLIENT]
    
    # Register humidifier services
    async def handle_water_full_reset(call: ServiceCall):
        await api.control_device(
            call.data["entity_id"],
            [{"command": "waterFullEvent", "value": 0}]
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_WATER_FULL_RESET,
        handle_water_full_reset,
        schema=vol.Schema({
            vol.Required("entity_id"): cv.entity_id
        })
    )

    # Entity setup
    entities = []
    for device_cfg in entry_data[CONF_DEVICES]:
        if device_cfg.get('type') in platform_device_types:
            try:
                coordinator = entry_data[CONF_COORDINATORS][device_cfg['device']]
                entity = GoveeHumidifier(hass, entry, coordinator, device_cfg)
                entities.append(entity)
            except Exception as e:
                _LOGGER.error("Error creating humidifier entity: %s", str(e))
    
    async_add_entities(entities, True)

class GoveeHumidifier(HumidifierEntity, GoveeLifePlatformEntity):
    """Advanced Govee Humidifier/Dehumidifier Entity"""
    
    _attr_has_entity_name = True
    _attr_supported_features = HumidifierEntityFeature.MODES
    _enable_turn_on_off_backwards_compatibility = False

    def _init_platform_specific(self, **kwargs):
        """Initialize humidifier-specific capabilities."""
        self._commands = {}
        self._water_full = False
        self._filter_life = None
        self._air_quality = None
        
        for cap in self._device_cfg.get('capabilities', []):
            self._process_capability(cap)
        
        # Set device class based on type
        self._attr_device_class = (
            HumidifierDeviceClass.DEHUMIDIFIER 
            if "dehumidifier" in self._device_cfg.get('type', '')
            else HumidifierDeviceClass.HUMIDIFIER
        )

    def _process_capability(self, cap: dict):
        """Process device capabilities."""
        instance = cap.get('instance', '')
        
        if cap['type'] == 'devices.capabilities.on_off':
            self._map_power_states(cap)
        
        elif cap['type'] == 'devices.capabilities.work_mode':
            self._process_work_mode(cap)
        
        elif cap['type'] == 'devices.capabilities.range' and instance == "humidity":
            self._setup_humidity_range(cap)
        
        elif cap['type'] == 'devices.capabilities.property':
            if instance == "filterLifeTime":
                self._commands["filter"] = partial(
                    self._send_filter_command,
                    instance=instance
                )
            elif instance == "airQuality":
                self._commands["air_quality"] = partial(
                    self._send_air_quality_command,
                    instance=instance
                )
        
        elif cap['type'] == 'devices.capabilities.event' and instance == "waterFullEvent":
            self._commands["water_reset"] = partial(
                self._send_water_reset_command,
                instance=instance
            )

    def _process_work_mode(self, cap: dict):
        """Process work mode capability structure."""
        self._attr_available_modes = []
        self._mode_mapping = {}
        
        for field in cap.get('parameters', {}).get('fields', []):
            if field['fieldName'] == 'workMode':
                for option in field.get('options', []):
                    mode_name = option['name']
                    self._attr_available_modes.append(mode_name)
                    self._mode_mapping[mode_name] = {
                        'workMode': option['value'],
                        'modeValue': None
                    }
            
            elif field['fieldName'] == 'modeValue':
                for value_opt in field.get('options', []):
                    if value_opt.get('options'):
                        # Nested gear modes
                        for gear_opt in value_opt['options']:
                            mode_name = gear_opt['name']
                            self._attr_available_modes.append(mode_name)
                            self._mode_mapping[mode_name] = {
                                'workMode': self._mode_mapping[value_opt['name']]['workMode'],
                                'modeValue': gear_opt['value']
                            }
                    else:
                        # Direct mode values
                        mode_name = value_opt['name']
                        self._attr_available_modes.append(mode_name)
                        self._mode_mapping[mode_name] = {
                            'workMode': self._mode_mapping[value_opt['name']]['workMode'],
                            'modeValue': value_opt.get('value', 0)
                        }

    @govee_error_retry
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the device on with optional mode setting."""
        commands = []
        
        if not self.is_on:
            commands.append(self._create_power_command(STATE_ON))
        
        if ATTR_MODE in kwargs:
            commands.append(self._create_mode_command(kwargs[ATTR_MODE]))
        
        if commands:
            await async_GoveeAPI_ControlDevice(
                self.hass,
                self._entry_id,
                self._device_cfg,
                commands
            )
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off."""
        await self._execute_command(self._create_power_command(STATE_OFF))

    async def async_set_humidity(self, humidity: int) -> None:
        """Set target humidity."""
        await self._execute_command({
            "type": "devices.capabilities.range",
            "instance": "humidity",
            "value": humidity
        })

    async def async_set_mode(self, mode: str) -> None:
        """Set device operation mode."""
        await self._execute_command(self._create_mode_command(mode))

    def _create_power_command(self, state: str) -> dict:
        return {
            "type": "devices.capabilities.on_off",
            "instance": "powerSwitch",
            "value": self._state_mapping_set[state]
        }

    def _create_mode_command(self, mode: str) -> dict:
        mode_data = self._mode_mapping.get(mode)
        if not mode_data:
            raise HomeAssistantError(f"Invalid mode: {mode}")
        
        return {
            "type": "devices.capabilities.work_mode",
            "instance": "workMode",
            "value": mode_data
        }

    async def _execute_command(self, command: dict):
        """Execute single command with error handling."""
        success = await async_GoveeAPI_ControlDevice(
            self.hass,
            self._entry_id,
            self._device_cfg,
            [command]
        )
        if success:
            self.async_write_ha_state()

    @property
    def target_humidity(self) -> int:
        """Return current target humidity."""
        return GoveeAPI_GetCachedStateValue(
            self.hass,
            self._entry_id,
            self.device_id,
            "devices.capabilities.range",
            "humidity"
        )

    @property
    def current_humidity(self) -> float:
        """Return current ambient humidity."""
        return GoveeAPI_GetCachedStateValue(
            self.hass,
            self._entry_id,
            self.device_id,
            "devices.capabilities.property",
            "humidity"
        )

    @property
    def extra_state_attributes(self) -> dict:
        """Return device-specific state attributes."""
        return {
            "filter_life": self._filter_life,
            "air_quality": self._air_quality,
            "water_full": self._water_full
        }

    async def async_update(self) -> None:
        """Update entity state from coordinator data."""
        await self._coordinator.async_request_refresh()
        device_data = self._coordinator.data.get(self.device_id, {})
        
        # Update core states
        self._attr_is_on = device_data.get('powerState') == STATE_ON
        self._attr_mode = device_data.get('currentMode')
        self._water_full = device_data.get('waterFull', False)
        
        # Update additional sensors
        self._filter_life = device_data.get('filterLife')
        self._air_quality = device_data.get('airQuality')
