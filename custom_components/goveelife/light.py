"""Light entities for the Govee Life integration."""

from __future__ import annotations
from typing import Final, Any
import logging
import asyncio
import math
from functools import partial

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback, async_call_later
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util.color import (
    brightness_to_value, 
    value_to_brightness,
    color_temperature_kelvin_to_mired,
    color_temperature_mired_to_kelvin
)
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import (
    CONF_DEVICES,
    STATE_ON,
    STATE_OFF,
    STATE_UNKNOWN,
)
from homeassistant.exceptions import HomeAssistantError

from .entities import GoveeLifePlatformEntity
from .const import (
    DOMAIN, 
    CONF_COORDINATORS,
    CONF_API_CLIENT,
    SCAN_INTERVAL
)
from .utils import (
    GoveeAPI_GetCachedStateValue,
    async_GoveeAPI_ControlDevice,
    govee_error_retry,
)

_LOGGER: Final = logging.getLogger(__name__)
platform = 'light'
platform_device_types = ['devices.types.light']

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up the light platform with services."""
    _LOGGER.debug("Setting up %s platform entry: %s", platform, entry.entry_id)
    entry_data = hass.data[DOMAIN][entry.entry_id]
    api = entry_data[CONF_API_CLIENT]
    
    # Register light services
    async def handle_set_segment_color(call: ServiceCall):
        await api.control_device(
            call.data["entity_id"],
            [{
                "command": "segmentedColorRgb",
                "value": {
                    "segment": call.data["segments"],
                    "rgb": (call.data["rgb"][0] << 16) + 
                           (call.data["rgb"][1] << 8) + 
                            call.data["rgb"][2]
                }
            }]
        )

    hass.services.async_register(
        DOMAIN,
        "set_segment_color",
        handle_set_segment_color,
        schema=vol.Schema({
            vol.Required("entity_id"): cv.entity_id,
            vol.Required("segments"): [int],
            vol.Required("rgb"): [int, int, int]
        })
    )

    # Existing entity setup code...
    entities = []
    for device_cfg in entry_data[CONF_DEVICES]:
        if device_cfg.get('type') in platform_device_types:
            try:
                coordinator = entry_data[CONF_COORDINATORS][device_cfg['device']]
                entity = GoveeLifeLight(hass, entry, coordinator, device_cfg)
                entities.append(entity)
            except Exception as e:
                _LOGGER.error("Error creating light entity: %s", str(e))
    
    async_add_entities(entities, True)

class GoveeLifeLight(LightEntity, GoveeLifePlatformEntity):
    """Advanced Govee Light Entity with full capability support."""
    
    _attr_has_entity_name = True
    _attr_supported_features = LightEntityFeature.EFFECT
    _enable_turn_on_off_backwards_compatibility = False

    def _init_platform_specific(self, **kwargs):
        """Initialize light-specific capabilities."""
        self._commands = {}
        self._effect_list = []
        self._segmented_capable = False
        self._music_mode_capable = False
        
        for cap in self._device_cfg.get('capabilities', []):
            self._process_capability(cap)
        
        self._update_color_modes()
        
    def _process_capability(self, cap: dict):
        """Process individual device capabilities."""
        instance = cap.get('instance', '')
        
        # Power state mapping
        if cap['type'] == 'devices.capabilities.on_off':
            self._map_power_states(cap)
        
        # Brightness handling
        elif cap['type'] == 'devices.capabilities.range' and instance == "brightness":
            self._setup_brightness(cap)
        
        # Color capabilities
        elif cap['type'] == 'devices.capabilities.color_setting':
            if instance == "colorRgb":
                self._setup_rgb_color(cap)
            elif instance == "colorTemperatureK":
                self._setup_color_temp(cap)
        
        # Segmented controls
        elif cap['type'] == 'devices.capabilities.segment_color_setting':
            self._segmented_capable = True
            self._commands["segment"] = partial(
                self._send_segment_command,
                instance=instance,
                struct_fields=cap['parameters']['fields']
            )
        
        # Scene management
        elif cap['type'] == 'devices.capabilities.dynamic_scene':
            self._effect_list.extend([
                opt['name'] for opt in cap.get('parameters', {}).get('options', [])
            ])
        
        # Music mode
        elif cap['type'] == 'devices.capabilities.music_setting' and instance == "musicMode":
            self._music_mode_capable = True
            self._commands["music"] = partial(
                self._send_music_command,
                struct_fields=cap['parameters']['fields']
            )

    def _update_color_modes(self):
        """Determine supported color modes according to HA guidelines."""
        color_modes = {ColorMode.ONOFF}
        
        if ColorMode.RGB in self._attr_supported_color_modes:
            color_modes.add(ColorMode.RGB)
        if ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            color_modes.add(ColorMode.COLOR_TEMP)
        if ColorMode.BRIGHTNESS in self._attr_supported_color_modes:
            color_modes.add(ColorMode.BRIGHTNESS)
        
        # HA requires specifying a single color mode if multiple are supported
        if len(color_modes) > 1:
            color_modes.discard(ColorMode.ONOFF)
        self._attr_supported_color_modes = color_modes

    @govee_error_retry
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on with advanced command batching."""
        commands = []
        should_power_on = not self.is_on
        
        # Build command sequence
        if should_power_on:
            commands.append(self._create_power_command(STATE_ON))
        
        if ATTR_BRIGHTNESS in kwargs:
            commands.append(self._create_brightness_command(kwargs[ATTR_BRIGHTNESS]))
        
        if ATTR_RGB_COLOR in kwargs:
            commands.append(self._create_rgb_command(kwargs[ATTR_RGB_COLOR]))
        
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            commands.append(self._create_color_temp_command(kwargs[ATTR_COLOR_TEMP_KELVIN]))
        
        # Execute all commands in single API call
        if commands:
            await async_GoveeAPI_ControlDevice(
                self.hass,
                self._entry_id,
                self._device_cfg,
                commands
            )
            self.async_write_ha_state()

    def _create_power_command(self, state: str) -> dict:
        return {
            "type": "devices.capabilities.on_off",
            "instance": "powerSwitch",
            "value": self._state_mapping_set[state]
        }

    def _create_brightness_command(self, brightness: int) -> dict:
        return {
            "type": "devices.capabilities.range",
            "instance": "brightness",
            "value": math.ceil(brightness_to_value(self._brightness_scale, brightness))
        }

    def _create_rgb_command(self, rgb: tuple) -> dict:
        return {
            "type": "devices.capabilities.color_setting",
            "instance": "colorRgb",
            "value": (rgb[0] << 16) + (rgb[1] << 8) + rgb[2]
        }

    def _create_color_temp_command(self, kelvin: int) -> dict:
        return {
            "type": "devices.capabilities.color_setting",
            "instance": "colorTemperatureK",
            "value": min(max(kelvin, self._attr_min_color_temp_kelvin), 
                       self._attr_max_color_temp_kelvin)
        }

    async def _send_segment_command(self, segments: list[int], rgb: tuple[int,int,int], **kwargs):
        """Handle segmented color control."""
        command = {
            "type": "devices.capabilities.segment_color_setting",
            "instance": kwargs.get('instance', 'segmentedColorRgb'),
            "value": {
                "segment": segments,
                "rgb": (rgb[0] << 16) + (rgb[1] << 8) + rgb[2]
            }
        }
        return await async_GoveeAPI_ControlDevice(
            self.hass,
            self._entry_id,
            self._device_cfg,
            [command]
        )

    async def _send_music_command(self, mode: str, sensitivity: int, **kwargs):
        """Handle music mode activation."""
        command = {
            "type": "devices.capabilities.music_setting",
            "instance": "musicMode",
            "value": {
                "musicMode": mode,
                "sensitivity": sensitivity,
                "autoColor": 1  # Default to auto-color unless specified
            }
        }
        return await async_GoveeAPI_ControlDevice(
            self.hass,
            self._entry_id,
            self._device_cfg,
            [command]
        )

    @property
    def effect_list(self) -> list[str]:
        """Return the list of supported effects."""
        return self._effect_list

    @property
    def extra_state_attributes(self) -> dict:
        """Return device-specific state attributes."""
        attrs = super().extra_state_attributes
        attrs.update({
            "segmented_control": self._segmented_capable,
            "music_mode": self._music_mode_capable
        })
        return attrs

    async def async_update(self) -> None:
        """Update entity state with coordinator data."""
        await self._coordinator.async_request_refresh()
        device_data = self._coordinator.data.get(self._device_cfg['device'], {})
        
        # Update all capabilities from fresh data
        self._attr_is_on = device_data.get('powerState') == STATE_ON
        self._attr_brightness = value_to_brightness(
            self._brightness_scale,
            device_data.get('brightness')
        )
        self._attr_rgb_color = self._getRGBfromI(
            device_data.get('colorRgb')
        )
        self._attr_color_temp_kelvin = device_data.get('colorTemperatureK')
        
        # Update effect if available
        current_scene = device_data.get('lightScene')
        if current_scene in self._effect_list:
            self._attr_effect = current_scene
