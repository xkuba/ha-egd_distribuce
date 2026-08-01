"""EG.D Distribuce – senzory."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import homeassistant.util.dt as dt_util

from .const import (
    CONF_EAN,
    COORDINATOR_KEY,
    CURRENCY_CZK,
    DOMAIN,
    METER_TYPE_AB,
    METER_TYPE_C1,
)
from .coordinator import EgdCoordinator

_LOGGER = logging.getLogger(__name__)

# Senzory záměrně NEMAJÍ state_class. Jejich hodnota je denní součet posledního
# dostupného dne, který se mění jednou denně a mezi dny klesá i roste – jako
# TOTAL_INCREASING by z toho HA odvozovalo nesmyslnou energii (pokles chápe jako
# reset měřidla). Do Energy Dashboardu patří externí statistika
# "egd_distribuce:<ean>_<profil>", kterou zapisuje coordinator po hodinách.


@dataclass(frozen=True)
class EgdSensorEntityDescription(SensorEntityDescription):
    """Popis senzoru EGD včetně klíče pro statistiku a omezení dle typu měřiče."""

    data_key: str = ""
    # None = platí pro všechny typy; jinak frozenset povolených typů
    meter_types: frozenset[str] | None = None


# Senzory společné pro všechny typy měřičů
_SENSORS_ALL: tuple[EgdSensorEntityDescription, ...] = (
    EgdSensorEntityDescription(
        key="consumption",
        data_key="consumption_kwh",
        name="Spotřeba ze sítě",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:transmission-tower-import",
        suggested_display_precision=2,
    ),
    EgdSensorEntityDescription(
        key="production",
        data_key="production_kwh",
        name="Dodávka do sítě (FVE)",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:transmission-tower-export",
        suggested_display_precision=2,
    ),
)

# Senzory pro sdílení energie a dodávku poníženou sdílením (A/B i C1)
_SENSORS_SHARING: tuple[EgdSensorEntityDescription, ...] = (
    EgdSensorEntityDescription(
        key="sharing_commercial",
        data_key="sharing_commercial_kwh",
        name="Sdílení energie – obchodní",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:account-group",
        suggested_display_precision=2,
    ),
    EgdSensorEntityDescription(
        key="sharing_distribution",
        data_key="sharing_distribution_kwh",
        name="Sdílení energie – distribuční",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:transmission-tower",
        suggested_display_precision=2,
    ),
    EgdSensorEntityDescription(
        key="production_sharing",
        data_key="production_sharing_kwh",
        name="Dodávka ponížená v rámci sdílení",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:transmission-tower-export",
        suggested_display_precision=2,
    ),
)

# Senzory jen pro A/B (jalová energie – profily IKC1, IMC1)
_SENSORS_AB: tuple[EgdSensorEntityDescription, ...] = (
    EgdSensorEntityDescription(
        key="reactive_consumption",
        data_key="reactive_consumption_kvarh",
        name="Jalová spotřeba",
        device_class=SensorDeviceClass.REACTIVE_ENERGY,
        native_unit_of_measurement="kvarh",
        icon="mdi:lightning-bolt",
        suggested_display_precision=2,
        meter_types=frozenset({METER_TYPE_AB}),
    ),
    EgdSensorEntityDescription(
        key="reactive_production",
        data_key="reactive_production_kvarh",
        name="Jalová dodávka",
        device_class=SensorDeviceClass.REACTIVE_ENERGY,
        native_unit_of_measurement="kvarh",
        icon="mdi:lightning-bolt-outline",
        suggested_display_precision=2,
        meter_types=frozenset({METER_TYPE_AB}),
    ),
)

SENSOR_DESCRIPTIONS: tuple[EgdSensorEntityDescription, ...] = (
    *_SENSORS_ALL,
    *_SENSORS_SHARING,
    *_SENSORS_AB,
)

# Senzory kolem ceny a tarifu – zakládají se jen když jsou nastavené ceny.
# Nemají data_key, hodnotu berou přímo z coordinatoru.
_SENSOR_MONTH_COST = SensorEntityDescription(
    key="month_cost",
    name="Náklady tento měsíc",
    device_class=SensorDeviceClass.MONETARY,
    native_unit_of_measurement=CURRENCY_CZK,
    icon="mdi:cash-multiple",
    suggested_display_precision=2,
)

_SENSOR_CURRENT_PRICE = SensorEntityDescription(
    key="current_price",
    name="Aktuální cena",
    native_unit_of_measurement=f"{CURRENCY_CZK}/kWh",
    icon="mdi:currency-usd",
    suggested_display_precision=2,
)

_SENSOR_TARIFF = SensorEntityDescription(
    key="tariff",
    name="Aktuální tarif",
    icon="mdi:toggle-switch-outline",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Nastaví senzory pro daný config entry – pouze senzory platné pro typ měřiče.

    Profily, pro které odběrné místo nemá data (typicky výroba a sdílení),
    se založí jako zakázané (disabled) – uživatel je může kdykoli povolit ručně.
    """
    coordinator: EgdCoordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR_KEY]
    ean = entry.data[CONF_EAN]
    meter_type = coordinator.meter_type
    available = coordinator.available_data_keys

    entities = [
        EgdSensor(
            coordinator,
            desc,
            ean,
            entry.entry_id,
            # available je None, dokud neproběhla synchronizace – pak nic nezakazujeme
            enabled_default=available is None or desc.data_key in available,
        )
        for desc in SENSOR_DESCRIPTIONS
        if desc.meter_types is None or meter_type in desc.meter_types
    ]
    async_add_entities(entities)

    if available is not None:
        _sync_registry_disabled_state(hass, entities, available)

    # Cenové senzory dávají smysl jen se zadanými cenami
    if coordinator.pricing_enabled:
        price_entities: list[SensorEntity] = [
            EgdMonthCostSensor(coordinator, _SENSOR_MONTH_COST, ean),
            EgdCurrentPriceSensor(coordinator, _SENSOR_CURRENT_PRICE, ean),
        ]
        # Tarif má smysl jen při dvoutarifu
        if coordinator.current_tariff() is not None:
            price_entities.append(EgdTariffSensor(coordinator, _SENSOR_TARIFF, ean))
        async_add_entities(price_entities)


@callback
def _sync_registry_disabled_state(
    hass: HomeAssistant,
    entities: list[EgdSensor],
    available: set[str],
) -> None:
    """Srovná stav již registrovaných entit s aktuální dostupností profilů.

    entity_registry_enabled_default se uplatní jen při prvním založení entity,
    proto u existujících entit upravíme registr přímo. Saháme výhradně na entity,
    které jsme zakázali sami (disabled_by INTEGRATION) – ruční volbu uživatele
    (disabled_by USER) neměníme.
    """
    registry = er.async_get(hass)

    for entity in entities:
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, entity.unique_id
        )
        if entity_id is None:
            continue

        registry_entry = registry.async_get(entity_id)
        if registry_entry is None:
            continue

        should_disable = entity.entity_description.data_key not in available

        if should_disable and registry_entry.disabled_by is None:
            _LOGGER.debug("EGD: zakazuji senzor %s – profil nemá data", entity_id)
            registry.async_update_entity(
                entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )
        elif (
            not should_disable
            and registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        ):
            _LOGGER.debug("EGD: povoluji senzor %s – profil má nově data", entity_id)
            registry.async_update_entity(entity_id, disabled_by=None)


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
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._ean = ean
        self._entry_id = entry_id

        # Unikátní ID = EAN + typ senzoru
        self._attr_unique_id = f"{ean}_{description.key}"

        # Profily bez dat (výroba, sdílení) zakládáme rovnou jako zakázané
        self._attr_entity_registry_enabled_default = enabled_default

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


class _EgdBaseSensor(CoordinatorEntity[EgdCoordinator], SensorEntity):
    """Společný základ pro senzory ceny a tarifu."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EgdCoordinator,
        description: SensorEntityDescription,
        ean: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._ean = ean
        self._attr_unique_id = f"{ean}_{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, ean)})


class EgdMonthCostSensor(_EgdBaseSensor):
    """Náklady za probíhající měsíc včetně stálé platby.

    state_class TOTAL s resetem k 1. dni měsíce je tu korektní – hodnota
    v rámci měsíce roste a na přelomu se vynuluje.
    """

    _attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> float | None:
        return self.coordinator.month_cost_total

    @property
    def last_reset(self) -> datetime | None:
        now = dt_util.now()
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        period = self.coordinator.price_list.for_date(dt_util.now().date())
        return {
            "naklady_za_energii": self.coordinator.month_energy_cost,
            "stala_platba": period.monthly_fee if period else None,
            "cenove_obdobi_od": (
                period.valid_from.isoformat() if period else None
            ),
            # Data z EG.D chodí se zpožděním jednoho dne
            "posledni_zapocteny_den": self.coordinator.data.get("dates", {}).get(
                "consumption_kwh"
            )
            if self.coordinator.data
            else None,
        }


class EgdCurrentPriceSensor(_EgdBaseSensor):
    """Cena za kWh platná právě teď (dle tarifu a cenového období)."""

    @property
    def native_value(self) -> float | None:
        return self.coordinator.current_price()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        period = self.coordinator.price_list.for_date(dt_util.now().date())
        if period is None:
            return {}
        return {
            "cena_vt": period.price_vt,
            "cena_nt": period.price_nt,
            "tarif": self.coordinator.current_tariff(),
            "cenove_obdobi_od": period.valid_from.isoformat(),
        }


class EgdTariffSensor(_EgdBaseSensor):
    """Aktuálně platný tarif (VT / NT) podle rozvrhu HDO."""

    @property
    def native_value(self) -> str | None:
        return self.coordinator.current_tariff()

    @property
    def icon(self) -> str:
        return (
            "mdi:toggle-switch"
            if self.coordinator.current_tariff() == "NT"
            else "mdi:toggle-switch-off-outline"
        )
