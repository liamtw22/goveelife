"""Light entities for the Govee Life integration."""

from __future__ import annotations
from typing import Final
import logging
import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
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

from .entities import GoveeLifePlatformEntity
from .const import DOMAIN, CONF_COORDINATORS
from .utils import GoveeAPI_GetCachedStateValue, async_GoveeAPI_ControlDevice

_LOGGER: Final = logging.getLogger(__name__)
PLATFORM = 'light'
PLATFORM_DEVICE_TYPES = ['devices.types.light']

def brightness_to_value(scale: tuple[int, int], brightness: int) -> int:
    """Convert Home Assistant brightness (0-255) to device value."""
    min_value, max_value = scale
    return round(((max_value - min_value) * (brightness / 255)) + min_value)

def value_to_brightness(scale: tuple[int, int], value: int | None) -> int | None:
    """Convert device value to Home Assistant brightness (0-255)."""
    if value is None:
        return None
    min_value, max_value = scale
    return round(((value - min_value) / (max_value - min_value)) * 255)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the light platform with Govee API support."""
    _LOGGER.debug("Setting up %s platform entry: %s | %s", PLATFORM, DOMAIN, entry.entry_id)
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
            entity = GoveeLifeLight(hass, entry, coordinator, device_cfg, platform=PLATFORM)
            entities.append(entity)
            await asyncio.sleep(0)
        except Exception as e:
            _LOGGER.error("%s - async_setup_entry %s: Setup failed: %s (%s.%s)",
                         entry.entry_id, PLATFORM, str(e), e.__class__.__module__, type(e).__name__)

    if entities:
        async_add_entities(entities)

class GoveeLifeLight(LightEntity, GoveeLifePlatformEntity):
    """Advanced light implementation with Govee API support."""
    
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator, device_cfg, **kwargs):
        """Initialize the light entity."""
        # Initialize all attributes before super().__init__
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF
        self._attr_effect_list = []
        self._scene_modes = {}
        self._music_modes = {}
        self._attr_supported_features = LightEntityFeature(0)
        self._brightness_scale = (1, 100)  # Default brightness range
        self._state_mapping = {}
        self._state_mapping_set = {}
        
        # Set default color temperature range
        self._attr_min_color_temp_kelvin = 2000
        self._attr_max_color_temp_kelvin = 9000
        
        super().__init__(hass, entry, coordinator, device_cfg, **kwargs)
        
        # Initial set of scenes
        self._set_default_scenes()

    def _set_default_scenes(self):
        """Set the default scene list."""
        scenes = [
            ("Sunrise", 196, 177), ("Sunset", 197, 178), ("Rainbow", 198, 179),
            ("Sunset Glow", 199, 180), ("Snow flake", 200, 181), ("Aurora", 201, 182),
            ("Forest", 202, 183), ("Ocean", 203, 184), ("Waves", 204, 185),
            ("Fire", 205, 186), ("Dark Clouds", 2457, 2565), ("Morning", 730, 784),
            ("Firefly", 2458, 2568), ("Sky", 731, 785), ("Flowing Light", 2459, 2569),
            ("Flower Field", 732, 786), ("Dense fog", 733, 787), ("Lightning", 734, 788),
            ("Falling Petals", 735, 789), ("Feather", 736, 790), ("Reading", 206, 187),
            ("Night Light", 207, 188), ("Fish tank", 208, 189), ("Graffiti", 209, 190),
            ("Cherry Blossom Festival", 210, 191), ("Eating Dots", 2460, 2570),
            ("Marshmallow", 2463, 2567), ("Goldfish", 737, 791), ("Geometry", 738, 792),
            ("Kaleidoscope", 739, 793), ("Rubik's Cube", 740, 794), ("Train", 741, 795),
            ("Kitchen Aromas", 742, 796), ("Rings", 743, 797), ("Dancing", 211, 192),
            ("Breathe", 212, 193), ("Gradient", 213, 194), ("Cheerful", 214, 195),
            ("Sweet", 215, 196), ("Heartbeat", 2462, 2571), ("Leisure", 744, 798),
            ("Healing", 745, 799), ("Dreamland", 746, 800)
        ]
        
        for name, scene_id, param_id in scenes:
            self._scene_modes[name] = {"id": scene_id, "paramId": param_id}
            if name not in self._attr_effect_list:
                self._attr_effect_list.append(name)

    def _init_platform_specific(self, **kwargs):
        """Initialize platform-specific capabilities."""
        capabilities = self._device_cfg.get('capabilities', [])
        
        for cap in capabilities:
            try:
                if cap['type'] == 'devices.capabilities.on_off':
                    self._handle_power_capability(cap)
                elif cap['type'] == 'devices.capabilities.range':
                    self._handle_range_capability(cap)
                elif cap['type'] == 'devices.capabilities.color_setting':
                    self._handle_color_capability(cap)
                elif cap['type'] == 'devices.capabilities.music_setting':
                    self._handle_music_capability(cap)
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
        """Handle brightness range capability."""
        if cap['instance'] == 'brightness':
            self._brightness_scale = (
                cap['parameters']['range']['min'],
                cap['parameters']['range']['max']
            )
            if ColorMode.ONOFF in self._attr_supported_color_modes:
                self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            else:
                self._attr_supported_color_modes.add(ColorMode.BRIGHTNESS)

    def _handle_color_capability(self, cap):
        """Handle color capabilities."""
        if cap['instance'] == 'colorRgb':
            self._attr_supported_color_modes.add(ColorMode.RGB)
            if ColorMode.COLOR_TEMP not in self._attr_supported_color_modes:
                self._attr_color_mode = ColorMode.RGB
                
        elif cap['instance'] == 'colorTemperatureK':
            self._attr_supported_color_modes.add(ColorMode.COLOR_TEMP)
            if ColorMode.RGB not in self._attr_supported_color_modes:
                self._attr_color_mode = ColorMode.COLOR_TEMP
                
            self._attr_min_color_temp_kelvin = cap['parameters']['range']['min']
            self._attr_max_color_temp_kelvin = cap['parameters']['range']['max']
            _LOGGER.debug("%s - Color temperature range: %d-%d K", self._identifier,
                         self._attr_min_color_temp_kelvin, self._attr_max_color_temp_kelvin)

    def _handle_music_capability(self, cap):
        """Handle music mode capabilities."""
        if cap['instance'] == 'musicMode':
            self._attr_supported_features |= LightEntityFeature.EFFECT
            for field in cap['parameters']['fields']:
                if field['fieldName'] == 'musicMode':
                    for option in field['options']:
                        mode_name = f"Music: {option['name']}"
                        if mode_name not in self._attr_effect_list:
                            self._attr_effect_list.append(mode_name)
                        self._music_modes[mode_name] = {
                            "musicMode": option['value'],
                            "sensitivity": 50,
                            "autoColor": 1
                        }

    @property
    def effect_list(self) -> list[str] | None:
        """Return the list of supported effects."""
        return self._attr_effect_list

    @property
    def effect(self) -> str | None:
        """Return the current effect."""
        # First check for scene
        scene = GoveeAPI_GetCachedStateValue(
            self.hass, self._entry_id, self._device_cfg['device'],
            'devices.capabilities.dynamic_scene', 'lightScene'
        )
        
        if scene:
            scene_id = scene.get('id') if isinstance(scene, dict) else None
            if scene_id:
                for name, value in self._scene_modes.items():
                    if value.get('id') == scene_id:
                        return name

        # Then check for music mode
        music = GoveeAPI_GetCachedStateValue(
            self.hass, self._entry_id, self._device_cfg['device'],
            'devices.capabilities.music_setting', 'musicMode'
        )
        if music:
            mode_value = music.get('musicMode')
            for name, config in self._music_modes.items():
                if config['musicMode'] == mode_value:
                    return name

        return None

    @property
    def brightness(self) -> int | None:
        """Return the brightness of this light between 0..255."""
        value = GoveeAPI_GetCachedStateValue(
            self.hass, self._entry_id, self._device_cfg['device'],
            'devices.capabilities.range', 'brightness'
        )
        return value_to_brightness(self._brightness_scale, value)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the rgb color value [int, int, int]."""
        value = GoveeAPI_GetCachedStateValue(
            self.hass, self._entry_id, self._device_cfg['device'],
            'devices.capabilities.color_setting', 'colorRgb'
        )
        if value is None:
            return None
        return (
            (value >> 16) & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF
        )

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temperature in Kelvin."""
        return GoveeAPI_GetCachedStateValue(
            self.hass, self._entry_id, self._device_cfg['device'],
            'devices.capabilities.color_setting', 'colorTemperatureK'
        )

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        value = GoveeAPI_GetCachedStateValue(
            self.hass, self._entry_id, self._device_cfg['device'],
            'devices.capabilities.on_off', 'powerSwitch'
        )
        return self._state_mapping.get(value) == STATE_ON

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the light on."""
        commands = []
        
        # Handle brightness
        if ATTR_BRIGHTNESS in kwargs:
            commands.append({
                "type": "devices.capabilities.range",
                "instance": "brightness",
                "value": brightness_to_value(self._brightness_scale, kwargs[ATTR_BRIGHTNESS])
            })
            
        # Handle color temperature
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            commands.append({
                "type": "devices.capabilities.color_setting",
                "instance": "colorTemperatureK",
                "value": kwargs[ATTR_COLOR_TEMP_KELVIN]
            })
            
        # Handle RGB color
        if ATTR_RGB_COLOR in kwargs:
            rgb = kwargs[ATTR_RGB_COLOR]
            rgb_value = (rgb[0] << 16) + (rgb[1] << 8) + rgb[2]
            commands.append({
                "type": "devices.capabilities.color_setting",
                "instance": "colorRgb",
                "value": rgb_value
            })
            
        # Handle effect
        if ATTR_EFFECT in kwargs:
            effect = kwargs[ATTR_EFFECT]
            
            if effect.startswith("Music:") and effect in self._music_modes:
                commands.append({
                    "type": "devices.capabilities.music_setting",
                    "instance": "musicMode",
                    "value": self._music_modes[effect]
                })
            elif effect in self._scene_modes:
                commands.append({
                    "type": "devices.capabilities.dynamic_scene",
                    "instance": "lightScene",
                    "value": self._scene_modes[effect]
                })
        
        # Always include power on command last if needed
        if not self.is_on or not commands:
            commands.append({
                "type": "devices.capabilities.on_off",
                "instance": "powerSwitch",
                "value": self._state_mapping_set[STATE_ON]
            })
        
        # Execute all commands
        for command in commands:
            _LOGGER.debug("%s - Sending command: %s", self._identifier, command)
            if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, command):
                self.async_write_ha_state()
            await asyncio.sleep(0.1)  # Small delay between commands

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the light off."""
        command = {
            "type": "devices.capabilities.on_off",
            "instance": "powerSwitch",
            "value": self._state_mapping_set[STATE_OFF]
        }
        
        if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, command):
            self.async_write_ha_state()
