"""Init for the Govee Life integration."""

from __future__ import annotations
from typing import Final
import logging
import asyncio
import json
import uuid

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.const import (
    CONF_API_KEY,
    CONF_DEVICES,
    CONF_PARAMS,
    CONF_SCAN_INTERVAL,
    CONF_STATE,
    CONF_TIMEOUT,
)
from homeassistant.helpers.typing import ConfigType
from homeassistant.components import webhook

from .const import (
    DOMAIN,
    CONF_COORDINATORS,
    FUNC_OPTION_UPDATES,
    SUPPORTED_PLATFORMS,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_TIMEOUT,
)
from .entities import (
    GoveeAPIUpdateCoordinator,
)
from .services import (
    async_registerService,
    async_service_SetPollInterval,
)
from .utils import (
    async_ProgrammingDebug,
    async_GoveeAPI_GETRequest,
    async_GoveeAPI_POSTRequest,
    async_GoveeAPI_GetDeviceState,
)

_LOGGER: Final = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Govee Life from configuration.yaml."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Govee Life from a config entry."""
    _LOGGER.debug("Setting up config entry: %s", entry.entry_id)

    try:
        # Initialize data stores
        _LOGGER.debug("%s - async_setup_entry: Creating data store: %s.%s", 
                     entry.entry_id, DOMAIN, entry.entry_id)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN].setdefault(entry.entry_id, {})
        entry_data = hass.data[DOMAIN][entry.entry_id]
        
        # Set up configuration parameters
        entry_data[CONF_PARAMS] = entry.data
        entry_data[CONF_SCAN_INTERVAL] = entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_POLL_INTERVAL)
        entry_data[CONF_TIMEOUT] = entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
    except Exception as e:
        _LOGGER.error("%s - async_setup_entry: Creating data store failed: %s (%s.%s)", 
                     entry.entry_id, str(e), e.__class__.__module__, type(e).__name__)
        return False

    try:
        # Get devices from API
        _LOGGER.debug("%s - async_setup_entry: Receiving cloud devices..", entry.entry_id)
        api_devices = await async_GoveeAPI_GETRequest(hass, entry.entry_id, 'user/devices')
        if api_devices is None:
            raise ConfigEntryAuthFailed("Failed to authenticate with Govee API")
        entry_data[CONF_DEVICES] = api_devices
    except Exception as e:
        _LOGGER.error("%s - async_setup_entry: Receiving cloud devices failed: %s (%s.%s)", 
                     entry.entry_id, str(e), e.__class__.__module__, type(e).__name__)
        raise ConfigEntryNotReady from e

    try:
        # Initialize device coordinators and subscribe to events
        _LOGGER.debug("%s - async_setup_entry: Creating update coordinators per device..", entry.entry_id)
        entry_data.setdefault(CONF_COORDINATORS, {})
        entry_data.setdefault(CONF_STATE, {})
        
        for device_cfg in api_devices:
            try:
                device = device_cfg.get('device')
                sku = device_cfg.get('sku')

                # Get initial device state
                await async_GoveeAPI_GetDeviceState(hass, entry.entry_id, device_cfg)
                
                # Create and initialize coordinator
                coordinator = GoveeAPIUpdateCoordinator(
                    hass, 
                    entry.entry_id, 
                    device_cfg,
                    scan_interval=entry_data[CONF_SCAN_INTERVAL],
                    timeout=entry_data[CONF_TIMEOUT]
                )
                await coordinator.async_config_entry_first_refresh()
                
                # Store coordinator
                entry_data[CONF_COORDINATORS][device] = coordinator

                # Subscribe to events if device has event capabilities
                if any(cap.get('type') == 'devices.capabilities.event' 
                      for cap in device_cfg.get('capabilities', [])):
                    try:
                        # Generate request ID
                        request_id = str(uuid.uuid4())

                        # Construct event subscription payload
                        payload = {
                            "requestId": request_id,
                            "payload": {
                                "device": device,
                                "sku": sku
                            }
                        }
                        
                        # Subscribe to device events
                        result = await async_GoveeAPI_POSTRequest(
                            hass,
                            entry.entry_id,
                            'device/event/subscribe',
                            payload
                        )
                        
                        if result:
                            _LOGGER.debug("%s - Successfully subscribed to events for device %s", 
                                        entry.entry_id, device)
                    except Exception as event_error:
                        _LOGGER.warning("%s - Failed to subscribe to events for device %s: %s",
                                      entry.entry_id, device, str(event_error))
                
                # Fetch additional scenes for light devices
                if device_cfg.get('type') == 'devices.types.light':
                    try:
                        scenes = await async_GoveeAPI_GETRequest(
                            hass,
                            entry.entry_id,
                            f'device/scenes?sku={sku}&device={device}'
                        )
                        if scenes and 'capabilities' in scenes:
                            device_capabilities = device_cfg.get('capabilities', [])
                            for scene_cap in scenes['capabilities']:
                                if scene_cap['type'] == 'devices.capabilities.dynamic_scene':
                                    # Replace or add scene capability
                                    found = False
                                    for i, cap in enumerate(device_capabilities):
                                        if (cap['type'] == 'devices.capabilities.dynamic_scene' and
                                            cap['instance'] == scene_cap['instance']):
                                            device_capabilities[i] = scene_cap
                                            found = True
                                            break
                                    if not found:
                                        device_capabilities.append(scene_cap)
                            device_cfg['capabilities'] = device_capabilities
                    except Exception as scene_error:
                        _LOGGER.warning("%s - Failed to fetch scenes for device %s: %s",
                                      entry.entry_id, device, str(scene_error))

            except Exception as device_error:
                _LOGGER.error("%s - Failed to initialize device %s: %s",
                            entry.entry_id, device_cfg.get('device'), str(device_error))
                continue

    except Exception as e:
        _LOGGER.error("%s - async_setup_entry: Creating update coordinators failed: %s (%s.%s)", 
                     entry.entry_id, str(e), e.__class__.__module__, type(e).__name__)
        raise ConfigEntryNotReady from e

    # Register webhook for event notifications
    webhook_id = entry.entry_id
    webhook.async_register(
        hass,
        DOMAIN,
        "Govee Event Webhook",
        webhook_id,
        handle_webhook,
    )
    entry_data['webhook_id'] = webhook_id
    _LOGGER.debug("%s - Registered webhook with ID: %s", entry.entry_id, webhook_id)

    try:
        # Register option update listener
        _LOGGER.debug("%s - async_setup_entry: Register option updates listener: %s", 
                     entry.entry_id, FUNC_OPTION_UPDATES)
        entry_data[FUNC_OPTION_UPDATES] = entry.add_update_listener(async_options_update_listener)
    except Exception as e:
        _LOGGER.error("%s - async_setup_entry: Register option updates listener failed: %s (%s.%s)", 
                     entry.entry_id, str(e), e.__class__.__module__, type(e).__name__)
        return False

    try:
        # Set up platforms
        await hass.config_entries.async_forward_entry_setups(entry, SUPPORTED_PLATFORMS)
    except Exception as e:
        _LOGGER.error("%s - async_setup_entry: Setup trigger for platform failed: %s (%s.%s)", 
                     entry.entry_id, str(e), e.__class__.__module__, type(e).__name__)
        return False

    try:
        # Register services
        _LOGGER.debug("%s - async_setup_entry: register services", entry.entry_id)
        await async_registerService(hass, "set_poll_interval", async_service_SetPollInterval)
    except Exception as e:
        _LOGGER.error("%s - async_setup_entry: register services failed: %s (%s.%s)", 
                     entry.entry_id, str(e), e.__class__.__module__, type(e).__name__)
        return False

    _LOGGER.debug("%s - async_setup_entry: Completed", entry.entry_id)
    return True

async def handle_webhook(hass: HomeAssistant, webhook_id: str, request) -> None:
    """Handle webhook calls from Govee."""
    try:
        body = await request.json()
        _LOGGER.debug("Received webhook data: %s", body)

        if not body:
            _LOGGER.warning("Empty webhook received")
            return

        if 'event' in body:
            event_data = body['event']
            device = event_data.get('device')
            if device:
                # Fire event for the device
                hass.bus.async_fire(f"{DOMAIN}_event", event_data)
                
                # Update device state
                for entry_id, entry_data in hass.data[DOMAIN].items():
                    if device in entry_data.get(CONF_STATE, {}):
                        # Update the device state with the event data
                        entry_data[CONF_STATE][device].update(event_data)
                        _LOGGER.debug("Updated state for device %s with event data", device)

    except Exception as e:
        _LOGGER.error("Error handling webhook: %s", str(e))

async def async_options_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    try:
        _LOGGER.debug("Unloading config entry: %s", entry.entry_id)

        # Unregister webhook
        webhook_id = hass.data[DOMAIN][entry.entry_id].get('webhook_id')
        if webhook_id:
            webhook.async_unregister(hass, webhook_id)
            _LOGGER.debug("Unregistered webhook: %s", webhook_id)

        # Unload platforms
        unload_ok = await hass.config_entries.async_unload_platforms(entry, SUPPORTED_PLATFORMS)

        if unload_ok:
            try:
                entry_data = hass.data[DOMAIN][entry.entry_id]
                
                # Stop update listener
                if FUNC_OPTION_UPDATES in entry_data:
                    entry_data[FUNC_OPTION_UPDATES]()
                
                # Remove entry data
                hass.data[DOMAIN].pop(entry.entry_id)
                
                # Remove domain data if empty
                if not hass.data[DOMAIN]:
                    hass.data.pop(DOMAIN)
                
            except Exception as e:
                _LOGGER.error("%s - async_unload_entry: Cleanup failed: %s (%s.%s)", 
                             entry.entry_id, str(e), e.__class__.__module__, type(e).__name__)
                return False

        return unload_ok
    except Exception as e:
        _LOGGER.error("%s - async_unload_entry: Unload failed: %s (%s.%s)", 
                     entry.entry_id, str(e), e.__class__.__module__, type(e).__name__)
        return False

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
