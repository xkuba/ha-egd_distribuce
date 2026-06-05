"""EG.D Distribuce – senzory."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_EAN, COORDINATOR_KEY, DOMAIN
from .coordinator import EgdCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EgdSensorEntityDescription(SensorEntityDescription):
    """Popis senzoru EGD včetně klíče pro statistiku."""

    data_key: str = ""


# Definice všech senzorů
SENSOR_DESCRIPTIONS: tuple[EgdSensorEntityDescription, ...] = (
    EgdSensorEntityDescription(
        key="consumption",
        data_key="consumption_kwh",
        name="Spotřeba ze sítě",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:transmission-tower-import",
        suggested_display_precision=2,
    ),
    EgdSensorEntityDescription(
        key="production",
        data_key="production_kwh",
        name="Dodávka do sítě (FVE)",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:transmission-tower-export",
        suggested_display_precision=2,
    ),
    EgdSensorEntityDescription(
        key="reactive_consumption",
        data_key="reactive_consumption_kvarh",
        name="Jalová spotřeba",
        device_class=SensorDeviceClass.REACTIVE_ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement="kvarh",
        icon="mdi:lightning-bolt",
        suggested_display_precision=2,
    ),
    EgdSensorEntityDescription(
        key="reactive_production",
        data_key="reactive_production_kvarh",
        name="Jalová dodávka",
        device_class=SensorDeviceClass.REACTIVE_ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement="kvarh",
        icon="mdi:lightning-bolt-outline",
        suggested_display_precision=2,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Nastaví senzory pro daný config entry."""
    coordinator: EgdCoordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR_KEY]
    ean = entry.data[CONF_EAN]

    entities = [
        EgdSensor(coordinator, description, ean, entry.entry_id)
        for description in SENSOR_DESCRIPTIONS
    ]
    async_add_entities(entities)


class EgdSensor(CoordinatorEntity[EgdCoordinator], SensorEntity):
    """Senzor EG.D Distribuce napojený na recorder statistiky."""

    entity_description: EgdSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EgdCoordinator,
        description: EgdSensorEntityDescription,
        ean: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._ean = ean
        self._entry_id = entry_id

        # Unikátní ID = EAN + typ senzoru
        self._attr_unique_id = f"{ean}_{description.key}"

        # Senzor je součástí device = jedno zařízení na EAN
        meter_label = f"Typ měření {coordinator.meter_type}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, ean)},
            name=f"EG.D Distribuce {ean}",
            manufacturer="EG.D (E.ON Distribuce)",
            model=meter_label,
            serial_number=ean,
            entry_type=None,
        )

        # statistic_id pro napojení na Energy Dashboard
        self._statistic_id = coordinator.get_statistic_id(description.data_key)

    @property
    def native_value(self) -> float | None:
        """Hodnota posledního dostupného dne (zobrazena jako stav senzoru)."""
        if self.coordinator.data:
            return self.coordinator.data.get("values", {}).get(self.entity_description.data_key)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Doplňující atributy senzoru."""
        attrs: dict[str, Any] = {
            "ean": self._ean,
            "statistic_id": self._statistic_id,
            "zdroj": "EG.D Distribuce24 OpenAPI",
            "profil_api": self.entity_description.data_key,
        }
        if self.coordinator.data:
            datum = self.coordinator.data.get("dates", {}).get(self.entity_description.data_key)
            if datum:
                attrs["datum"] = datum
        return attrs

    @property
    def available(self) -> bool:
        """Senzor je vždy dostupný – data jsou historická."""
        return True
