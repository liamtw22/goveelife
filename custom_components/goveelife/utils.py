"""Helper functions for Govee Life."""

from __future__ import annotations
from typing import Final
import logging
import asyncio
import json
import uuid
import requests

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import (
    ATTR_DATE,
    CONF_API_KEY,
    CONF_COUNT,
    CONF_PARAMS,
    CONF_STATE,
    CONF_TIMEOUT,
)

from .const import (
    DOMAIN,
    CONF_API_COUNT,
    CLOUD_API_URL_OPENAPI,
    CLOUD_API_HEADER_KEY,
)

_LOGGER: Final = logging.getLogger(__name__)

async def async_ProgrammingDebug(obj, show_all:bool=False) -> None:
    """Async: return all attributes of a specific object.""" 
    try:
        _LOGGER.debug("%s - async_ProgrammingDebug: %s", DOMAIN, obj)
        for attr in dir(obj):
            if attr.startswith('_') and not show_all:
                continue
            if hasattr(obj, attr):
                _LOGGER.debug("%s - async_ProgrammingDebug: %s = %s", DOMAIN, attr, getattr(obj, attr))
            await asyncio.sleep(0)
    except Exception as e:
        _LOGGER.error("%s - async_ProgrammingDebug: failed: %s (%s.%s)", 
                     DOMAIN, str(e), e.__class__.__module__, type(e).__name__)
        pass

async def async_GooveAPI_CountRequests(hass: HomeAssistant, entry_id: str) -> None:
    """Async: Count daily number of requests to GooveAPI."""       
    try:
        entry_data = hass.data[DOMAIN][entry_id]
        today = date.today()
        v = entry_data.get(CONF_API_COUNT, {CONF_COUNT: 0, ATTR_DATE: today})        
        if v[ATTR_DATE] == today:
            v[CONF_COUNT] = int(v[CONF_COUNT]) + 1
        else:
            v[CONF_COUNT] = 1           
        entry_data[CONF_API_COUNT] = v
        
        _LOGGER.debug("%s - async_GooveAPI_CountRequests: %s -> %s", 
                     entry_id, v[ATTR_DATE], v[CONF_COUNT])
    except Exception as e:
        _LOGGER.error("%s - async_GooveAPI_CountRequests: Failed: %s (%s.%s)", 
                     entry_id, str(e), e.__class__.__module__, type(e).__name__)
        return None

async def async_GoveeAPI_GETRequest(hass: HomeAssistant, entry_id: str, path: str) -> None:
    """Async: Request device list via GooveAPI."""
    try:
        _LOGGER.debug("%s - async_GoveeAPI_GETRequest: perform api request", entry_id)
        entry_data = hass.data[DOMAIN][entry_id]
        
        headers = {
            "Content-Type": "application/json",
            CLOUD_API_HEADER_KEY: str(entry_data[CONF_PARAMS].get(CONF_API_KEY))
        }
        timeout = entry_data[CONF_PARAMS].get(CONF_TIMEOUT)
        url = CLOUD_API_URL_OPENAPI + '/' + path.strip("/")

        await async_GooveAPI_CountRequests(hass, entry_id)
        response = await hass.async_add_executor_job(
            lambda: requests.get(
                url,
                headers=headers,
                timeout=timeout
            )
        )        
        
        if response.status_code == 429:
            _LOGGER.error("%s - async_GoveeAPI_GETRequest: Too many API request - limit is 10000/Account/Day", 
                         entry_id)
            return None
        elif response.status_code == 401:
            _LOGGER.error("%s - async_GoveeAPI_GETRequest: Unauthorized - check your API Key", 
                         entry_id)
            return None
        elif response.status_code != 200:
            _LOGGER.error("%s - async_GoveeAPI_GETRequest: Failed: %s", 
                         entry_id, response.text)
            return None

        return response.json().get('data')

    except Exception as e:
        _LOGGER.error("%s - async_GoveeAPI_GETRequest: Failed: %s (%s.%s)", 
                     entry_id, str(e), e.__class__.__module__, type(e).__name__)
        return None

async def async_GoveeAPI_POSTRequest(hass: HomeAssistant, entry_id: str, path: str, data: dict) -> dict | None:
    """Async: Perform post request via GoveeAPI."""
    try:
        entry_data = hass.data[DOMAIN][entry_id]
        
        headers = {
            "Content-Type": "application/json",
            CLOUD_API_HEADER_KEY: str(entry_data[CONF_PARAMS].get(CONF_API_KEY))
        }
        timeout = entry_data[CONF_PARAMS].get(CONF_TIMEOUT)
        url = CLOUD_API_URL_OPENAPI + '/' + path.strip("/")
        
        # Ensure request ID is present
        if 'requestId' not in data:
            data['requestId'] = str(uuid.uuid4())
            
        _LOGGER.debug("%s - async_GoveeAPI_POSTRequest: URL=%s, Data=%s", 
                     entry_id, url, json.dumps(data))
        
        await async_GooveAPI_CountRequests(hass, entry_id)
        response = await hass.async_add_executor_job(
            lambda: requests.post(
                url,
                json=data,
                headers=headers,
                timeout=timeout
            )
        )
        
        if response.status_code == 429:
            _LOGGER.error("%s - async_GoveeAPI_POSTRequest: Too many API requests - limit is 10000/Account/Day", 
                         entry_id)
            return None
        elif response.status_code == 401:
            _LOGGER.error("%s - async_GoveeAPI_POSTRequest: Unauthorized - check your API Key", 
                         entry_id)
            return None
        elif response.status_code != 200:
            _LOGGER.error("%s - async_GoveeAPI_POSTRequest: Failed: %s", 
                         entry_id, response.text)
            return None

        return response.json()
    except Exception as e:
        _LOGGER.error("%s - async_GoveeAPI_POSTRequest: Failed: %s (%s.%s)", 
                     entry_id, str(e), e.__class__.__module__, type(e).__name__)
        return None

async def async_GoveeAPI_GetDeviceState(hass: HomeAssistant, entry_id: str, device_cfg: dict, return_status_code=False) -> None:
    """Async: Request and save state of device via GoveeAPI."""
    try:
        _LOGGER.debug("%s - async_GoveeAPI_GetDeviceState: preparing request for device %s", 
                     entry_id, device_cfg.get('device'))

        # Construct device state request
        payload = {
            "requestId": str(uuid.uuid4()),
            "payload": {
                "sku": device_cfg.get('sku'),
                "device": device_cfg.get('device')
            }
        }
        
        # Get device state
        result = await async_GoveeAPI_POSTRequest(
            hass,
            entry_id,
            'device/state',
            payload
        )

        if isinstance(result, int) and return_status_code:
            return result
        
        if result is None:
            return False

        # Store device state
        entry_data = hass.data[DOMAIN][entry_id]
        entry_data.setdefault(CONF_STATE, {})
        device = device_cfg.get('device')
        entry_data[CONF_STATE][device] = result.get('payload', {})
        
        return True
    except Exception as e:
        _LOGGER.error("%s - async_GoveeAPI_GetDeviceState: Failed: %s (%s.%s)", 
                     entry_id, str(e), e.__class__.__module__, type(e).__name__)
        return False

async def async_GoveeAPI_ControlDevice(hass: HomeAssistant, entry_id: str, device_cfg: dict, state_capability: dict) -> None:
    """Async: Control device via GoveeAPI."""
    try:
        device = device_cfg.get('device')
        _LOGGER.debug("%s - async_GoveeAPI_ControlDevice: Sending command to device %s: %s", 
                     entry_id, device, json.dumps(state_capability))

        # Construct device control request
        payload = {
            "requestId": str(uuid.uuid4()),
            "payload": {
                "sku": device_cfg.get('sku'),
                "device": device,
                "capability": state_capability
            }
        }
        
        # Send control command
        result = await async_GoveeAPI_POSTRequest(
            hass,
            entry_id,
            'device/control',
            payload
        )

        if result is None:
            return False
            
        # Update device state with command result
        entry_data = hass.data[DOMAIN][entry_id]
        if 'capability' in result:
            new_cap = result['capability']
            # Move value to state
            value = new_cap.pop('value', None)
            if value is not None:
                new_cap['state'] = {"value": value}
            
            # Update capability in device state
            device_state = entry_data.get(CONF_STATE, {}).get(device, {})
            capabilities = device_state.get('capabilities', [])
            
            # Find and update matching capability
            for i, cap in enumerate(capabilities):
                if (cap['type'] == new_cap['type'] and 
                    cap['instance'] == new_cap['instance']):
                    capabilities[i] = new_cap
                    break
            
            _LOGGER.debug("%s - Updated state for device %s with capability: %s", 
                         entry_id, device, json.dumps(new_cap))
            return True

        return False
    except Exception as e:
        _LOGGER.error("%s - async_GoveeAPI_ControlDevice: Failed: %s (%s.%s)", 
                     entry_id, str(e), e.__class__.__module__, type(e).__name__)
        return False

def GoveeAPI_GetCachedStateValue(hass: HomeAssistant, entry_id: str, device_id: str, value_type: str, value_instance: str):
    """Get value from cached device state."""
    try:
        entry_data = hass.data[DOMAIN][entry_id]
        capabilities = entry_data.get(CONF_STATE, {}).get(device_id, {}).get('capabilities', [])
        
        for cap in capabilities:
            if cap['type'] == value_type and cap['instance'] == value_instance:
                cap_state = cap.get('state')
                if cap_state is not None:
                    return cap_state.get('value', cap_state.get(value_instance))
                    
        return None
    except Exception as e:
        _LOGGER.error("%s - GoveeAPI_GetCachedStateValue: Failed: %s (%s.%s)", 
                     entry_id, str(e), e.__class__.__module__, type(e).__name__)
        return None
