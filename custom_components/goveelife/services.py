"""Service support for advanced Govee device features."""

from __future__ import annotations
from typing import Final
import logging
import asyncio
import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_ENTITY_ID,
    CONF_MODE,
    CONF_NAME,
)

from .const import (
    DOMAIN,
    CONF_ENTRY_ID,
    CONF_SEGMENT,
    CONF_BRIGHTNESS,
    CONF_COLOR,
    CONF_SENSITIVITY,
    CONF_AUTO_COLOR,
    CONF_EFFECT,
    CONF_SPEED
)
from .utils import async_GoveeAPI_ControlDevice

_LOGGER: Final = logging.getLogger(__name__)

async def async_registerService(hass: HomeAssistant, name: str, service) -> None:
    """Register a service if it doesn't exist."""
    try:
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(DOMAIN, name, service)
    except Exception as e:
        _LOGGER.error("Service registration failed: %s", str(e))

async def async_setup_services(hass: HomeAssistant) -> None:
    """Register all advanced services."""
    services = [
        ('set_poll_interval', async_service_SetPollInterval),
        ('control_segment', async_service_ControlSegment),
        ('set_music_mode', async_service_SetMusicMode),
        ('reset_water_alert', async_service_ResetWaterAlert),
        ('set_custom_mode', async_service_SetCustomMode),
        ('save_snapshot', async_service_SaveSnapshot),
        ('restore_snapshot', async_service_RestoreSnapshot)
    ]
    
    for service_name, handler in services:
        await async_registerService(hass, service_name, handler)

async def async_service_SetPollInterval(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service to set API poll interval."""
    entry_id = call.data.get(CONF_ENTRY_ID)
    interval = call.data.get(CONF_SPEED)
    
    if not entry_id or not interval:
        _LOGGER.error("Missing required parameters")
        return

    try:
        hass.data[DOMAIN][entry_id][CONF_SPEED] = interval
        _LOGGER.info("Poll interval updated to %s seconds", interval)
    except Exception as e:
        _LOGGER.error("Failed to update poll interval: %s", str(e))

async def async_service_ControlSegment(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service for segmented light control."""
    device_id = call.data.get(CONF_DEVICE_ID)
    segment = call.data.get(CONF_SEGMENT)
    brightness = call.data.get(CONF_BRIGHTNESS)
    color = call.data.get(CONF_COLOR)
    
    if not device_id or not segment:
        _LOGGER.error("Missing required parameters")
        return

    capability = {
        "type": "devices.capabilities.segment_color_setting",
        "instance": "segmentedColorRgb" if color else "segmentedBrightness",
        "value": {
            "segment": segment,
            "rgb": color,
            "brightness": brightness
        }
    }
    
    await _execute_device_command(hass, call, capability)

async def async_service_SetMusicMode(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service for music response mode configuration."""
    mode = call.data.get(CONF_MODE)
    sensitivity = call.data.get(CONF_SENSITIVITY, 50)
    auto_color = call.data.get(CONF_AUTO_COLOR, True)
    color = call.data.get(CONF_COLOR)
    
    capability = {
        "type": "devices.capabilities.music_setting",
        "instance": "musicMode",
        "value": {
            "musicMode": mode,
            "sensitivity": sensitivity,
            "autoColor": 1 if auto_color else 0,
            "rgb": color
        }
    }
    
    await _execute_device_command(hass, call, capability)

async def async_service_ResetWaterAlert(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service to reset water full alert."""
    capability = {
        "type": "devices.capabilities.event",
        "instance": "waterFullEvent",
        "value": 0
    }
    
    await _execute_device_command(hass, call, capability)

async def async_service_SetCustomMode(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service to set custom work modes."""
    mode_name = call.data.get(CONF_NAME)
    mode_value = call.data.get("value")
    
    capability = {
        "type": "devices.capabilities.work_mode",
        "instance": "workMode",
        "value": {
            "workMode": mode_name,
            "modeValue": mode_value
        }
    }
    
    await _execute_device_command(hass, call, capability)

async def async_service_SaveSnapshot(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service to save current device state."""
    capability = {
        "type": "devices.capabilities.dynamic_scene",
        "instance": "snapshot",
        "value": 1
    }
    
    await _execute_device_command(hass, call, capability)

async def async_service_RestoreSnapshot(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service to restore saved snapshot."""
    capability = {
        "type": "devices.capabilities.dynamic_scene",
        "instance": "snapshot",
        "value": 2
    }
    
    await _execute_device_command(hass, call, capability)

async def _execute_device_command(hass: HomeAssistant, call: ServiceCall, capability: dict) -> None:
    """Execute a device command with error handling."""
    try:
        entry_id = call.data.get(CONF_ENTRY_ID)
        device_id = call.data.get(CONF_DEVICE_ID)
        
        if not entry_id or not device_id:
            _LOGGER.error("Missing entry_id or device_id")
            return

        entry_data = hass.data[DOMAIN][entry_id]
        device_cfg = next((d for d in entry_data[CONF_DEVICES] if d['device'] == device_id), None)
        
        if not device_cfg:
            _LOGGER.error("Device not found: %s", device_id)
            return

        success = await async_GoveeAPI_ControlDevice(
            hass,
            entry_id,
            device_cfg,
            capability
        )
        
        if not success:
            _LOGGER.error("Failed to execute command: %s", capability)
            
    except Exception as e:
        _LOGGER.error("Service execution failed: %s", str(e))
