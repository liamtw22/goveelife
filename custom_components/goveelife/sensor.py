"""Sensor entities for the Govee Life integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final
import logging
import asyncio

from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
    SensorEntityDescription,
)
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import (
    CONF_DEVICES,
    STATE_UNKNOWN,
    PERCENTAGE,
)

from .entities import GoveeLifePlatformEntity
from .const import DOMAIN, CONF_COORDINATORS
from .utils import GoveeAPI_GetCachedStateValue

_LOGGER: Final = logging.getLogger(__name__)
PLATFORM = 'sensor'

@dataclass
class GoveeSensorDescriptionMixin:
    """Mixin for Govee sensor description."""
    value_fn: Callable[[Any], Any]

@dataclass
class GoveeSensorDescription(SensorEntityDescription, GoveeSensorDescriptionMixin):
    """Description for Govee sensor."""

@dataclass
class GoveeBinarySensorDescription(BinarySensorEntityDescription, GoveeSensorDescriptionMixin):
    """Description for Govee binary sensor."""

SENSOR_TYPES: tuple[GoveeSensorDescription, ...] = (
    GoveeSensorDescription(
        key="filter_life",
        name="Filter Life",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:air-filter",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state,
    ),
    GoveeSensorDescription(
        key="air_quality",
        name="Air Quality Index",
        native_unit_of_measurement="AQI",
        icon="mdi:air-quality-good",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state,
    ),
)

BINARY_SENSOR_TYPES: tuple[GoveeBinarySensorDescription, ...] = (
    GoveeBinarySensorDescription(
        key="water_tank",
        name="Water Tank",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state,
    ),
)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up sensors."""
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
            device = device_cfg.get('device')
            coordinator = entry_data[CONF_COORDINATORS][device]

            # Add standard sensors based on capabilities
            for capability in device_cfg.get('capabilities', []):
                if capability['type'] == 'devices.capabilities.property':
                    if capability['instance'] == 'filterLifeTime':
                        for description in SENSOR_TYPES:
                            if description.key == "filter_life":
                                entities.append(GoveeSensor(coordinator, device_cfg, entry, description))
                    elif capability['instance'] == 'airQuality':
                        for description in SENSOR_TYPES:
                            if description.key == "air_quality":
                                entities.append(GoveeSensor(coordinator, device_cfg, entry, description))

            # Add water tank sensor if supported
            if any(cap['type'] == 'devices.capabilities.event' and
                  cap['instance'] == 'waterFullEvent'
                  for cap in device_cfg.get('capabilities', [])):
                entities.append(GoveeBinarySensor(coordinator, device_cfg, entry, BINARY_SENSOR_TYPES[0]))

            await asyncio.sleep(0)
        except Exception as e:
            _LOGGER.error("%s - async_setup_entry %s: Failed to setup device: %s (%s.%s)",
                         entry.entry_id, PLATFORM, str(e), e.__class__.__module__, type(e).__name__)

    if entities:
        async_add_entities(entities)

class GoveeSensor(GoveeLifePlatformEntity, SensorEntity):
    """Implementation of a Govee sensor."""

    entity_description: GoveeSensorDescription

    def __init__(
        self,
        coordinator,
        device_cfg: dict[str, Any],
        config_entry: ConfigEntry,
        description: GoveeSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        self.entity_description = description
        
        # Get base name without device type suffix
        device_name = device_cfg.get('deviceName', '')
        base_name = (device_name.replace(' Air Purifier', '')
                              .replace(' Dehumidifier', '')
                              .replace(' Fan', '')
                              .strip())
        
        # Set entity name
        self._attr_name = f"{base_name} {description.name}"
        self._attr_unique_id = f"{device_cfg.get('device')}_{description.key}"
        
        kwargs = {'platform': PLATFORM}
        super().__init__(hass=coordinator.hass, entry=config_entry, coordinator=coordinator, device_cfg=device_cfg, **kwargs)

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        if self.entity_description.key == "filter_life":
            state = GoveeAPI_GetCachedStateValue(
                self.hass,
                self._entry_id,
                self._device_cfg['device'],
                'devices.capabilities.property',
                'filterLifeTime'
            )
        elif self.entity_description.key == "air_quality":
            state = GoveeAPI_GetCachedStateValue(
                self.hass,
                self._entry_id,
                self._device_cfg['device'],
                'devices.capabilities.property',
                'airQuality'
            )
        else:
            state = None
            
        return self.entity_description.value_fn(state) if state is not None else None

class GoveeBinarySensor(GoveeLifePlatformEntity, BinarySensorEntity):
    """Implementation of a Govee binary sensor."""

    entity_description: GoveeBinarySensorDescription

    def __init__(
        self,
        coordinator,
        device_cfg: dict[str, Any],
        config_entry: ConfigEntry,
        description: GoveeBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        self.entity_description = description
        
        # Get base name without device type suffix
        device_name = device_cfg.get('deviceName', '')
        base_name = (device_name.replace(' Air Purifier', '')
                              .replace(' Dehumidifier', '')
                              .replace(' Fan', '')
                              .strip())
        
        # Set entity name
        self._attr_name = f"{base_name} {description.name}"
        self._attr_unique_id = f"{device_cfg.get('device')}_{description.key}"
        self._state = False
        self._attr_extra_state_attributes = {}
        
        kwargs = {'platform': PLATFORM}
        super().__init__(hass=coordinator.hass, entry=config_entry, coordinator=coordinator, device_cfg=device_cfg, **kwargs)

        # Set up event listener
        coordinator.hass.bus.async_listen(f"{DOMAIN}_event", self._handle_event)

    @callback
    def _handle_event(self, event):
        """Handle device events."""
        if event.data.get('device') != self._device_cfg.get('device'):
            return
            
        try:
            if 'waterFullEvent' in event.data:
                self._state = bool(event.data['waterFullEvent'])
                if self._state:
                    _LOGGER.warning("%s - Water tank is full", self._attr_name)
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("%s - Failed to handle event: %s", self._attr_name, str(e))

    @property
    def is_on(self) -> bool:
        """Return true if water tank is full."""
        return self._state
