"""EG.D Distribuce – Config Flow (GUI nastavení)."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EgdApi, EgdApiError, EgdAuthError, EgdPermissionError, EgdUnsupportedMeterError
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_EAN,
    CONF_HDO_A,
    CONF_HDO_B,
    CONF_HDO_CODE,
    CONF_HDO_DP,
    CONF_HDO_MODE,
    CONF_HDO_PSC,
    CONF_HDO_VARIANT,
    CONF_HISTORY_FROM,
    CONF_METER_TYPE,
    CONF_PRICE_PERIODS,
    CONF_TEST_MODE,
    CONF_UPDATE_HOUR,
    DEFAULT_SCAN_DAYS,
    DEFAULT_UPDATE_HOUR,
    DOMAIN,
    HDO_MODE_CLASSIC,
    HDO_MODE_NONE,
    HDO_MODE_SMART,
)
from .hdo import HdoClient, HdoError
from .pricing import (
    KEY_MONTHLY_FEE,
    KEY_PRICE_NT,
    KEY_PRICE_VT,
    KEY_VALID_FROM,
    PriceList,
    PricePeriod,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): str,
        vol.Required(CONF_CLIENT_SECRET): str,
        vol.Required(CONF_EAN): str,
        vol.Required(CONF_TEST_MODE, default=False): bool,
        vol.Optional(CONF_HISTORY_FROM): str,
    }
)


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Ověří přihlašovací údaje a zjistí typ měřiče z /om."""
    session = async_get_clientsession(hass)
    api = EgdApi(
        session=session,
        client_id=data[CONF_CLIENT_ID],
        client_secret=data[CONF_CLIENT_SECRET],
        test_mode=data.get(CONF_TEST_MODE, False),
    )
    meter_type = await api.async_validate_credentials(data[CONF_EAN])
    return {
        "title": f"EG.D Distribuce – {data[CONF_EAN]}",
        CONF_METER_TYPE: meter_type,
    }


class EgdDistribuceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow pro EG.D Distribuce."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Krok 1 – zadání přihlašovacích údajů."""
        errors: dict[str, str] = {}

        if user_input is not None:
            history_from_str = user_input.get(CONF_HISTORY_FROM, "").strip()
            if history_from_str:
                try:
                    date.fromisoformat(history_from_str)
                except ValueError:
                    errors[CONF_HISTORY_FROM] = "invalid_date"

            if not errors:
                await self.async_set_unique_id(
                    user_input[CONF_EAN], raise_on_progress=False
                )
                self._abort_if_unique_id_configured()

                try:
                    info = await _validate_input(self.hass, user_input)
                except EgdAuthError:
                    errors["base"] = "invalid_auth"
                except EgdUnsupportedMeterError:
                    errors["base"] = "unsupported_meter"
                except EgdPermissionError:
                    errors["base"] = "invalid_ean"
                except EgdApiError:
                    errors["base"] = "cannot_connect"
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("EGD: neočekávaná chyba při validaci")
                    errors["base"] = "unknown"
                else:
                    entry_data = {
                        k: v for k, v in user_input.items() if k != CONF_HISTORY_FROM
                    }
                    options = (
                        {CONF_HISTORY_FROM: history_from_str} if history_from_str else {}
                    )
                    return self.async_create_entry(
                        title=info["title"],
                        data={**entry_data, CONF_METER_TYPE: info[CONF_METER_TYPE]},
                        options=options,
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
            description_placeholders={
                "portal_url": "portal.distribuce24.cz",
                "path": "Správa účtů → Vzdálený přístup – OPENAPI",
            },
        )

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Re-autentizace pokud token/secret přestane fungovat."""
        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> EgdOptionsFlowHandler:
        """Vrátí options flow handler."""
        return EgdOptionsFlowHandler()


class EgdOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow – stahování, tarif HDO a cenová období.

    Změny se hromadí v self._options a ukládají se až volbou „Uložit a zavřít",
    aby šlo v jednom průchodu přidat víc cenových období.
    """

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._hdo_records: list[dict[str, Any]] = []

    @property
    def _prices(self) -> PriceList:
        return PriceList.from_options(self._options.get(CONF_PRICE_PERIODS))

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Rozcestník nastavení."""
        if not self._options:
            self._options = dict(self.config_entry.options)

        return self.async_show_menu(
            step_id="init",
            menu_options=["zakladni", "hdo", "ceny", "ulozit"],
        )

    async def async_step_ulozit(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Uloží nastavení a zavře dialog."""
        return self.async_create_entry(title="", data=self._options)

    # ------------------------------------------------------------------
    # Základní nastavení
    # ------------------------------------------------------------------

    async def async_step_zakladni(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Hodina stahování a počátek historie."""
        errors: dict[str, str] = {}

        if user_input is not None:
            history_from_str = user_input.get(CONF_HISTORY_FROM, "").strip()
            if history_from_str:
                try:
                    date.fromisoformat(history_from_str)
                except ValueError:
                    errors[CONF_HISTORY_FROM] = "invalid_date"

            if not errors:
                self._options[CONF_UPDATE_HOUR] = user_input[CONF_UPDATE_HOUR]
                self._options[CONF_HISTORY_FROM] = history_from_str
                return await self.async_step_init()

        default_history = (date.today() - timedelta(days=DEFAULT_SCAN_DAYS)).isoformat()
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_HOUR,
                    default=self._options.get(CONF_UPDATE_HOUR, DEFAULT_UPDATE_HOUR),
                ): vol.All(int, vol.Range(min=0, max=23)),
                vol.Optional(
                    CONF_HISTORY_FROM,
                    description={
                        "suggested_value": self._options.get(
                            CONF_HISTORY_FROM, default_history
                        )
                    },
                ): str,
            }
        )
        return self.async_show_form(
            step_id="zakladni", data_schema=schema, errors=errors
        )

    # ------------------------------------------------------------------
    # Tarif HDO
    # ------------------------------------------------------------------

    async def async_step_hdo(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Volba způsobu určení tarifu a zadání kódu."""
        errors: dict[str, str] = {}

        if user_input is not None:
            mode = user_input[CONF_HDO_MODE]
            self._options[CONF_HDO_MODE] = mode

            if mode == HDO_MODE_NONE:
                for key in (
                    CONF_HDO_CODE, CONF_HDO_PSC, CONF_HDO_A,
                    CONF_HDO_B, CONF_HDO_DP, CONF_HDO_VARIANT,
                ):
                    self._options.pop(key, None)
                return await self.async_step_init()

            client = HdoClient(async_get_clientsession(self.hass))
            try:
                if mode == HDO_MODE_SMART:
                    code = user_input.get(CONF_HDO_CODE, "").strip()
                    if not code:
                        raise HdoError("Kód HDO nebyl zadán")
                    records = await client.async_match_smart(code)
                    self._options[CONF_HDO_CODE] = code
                else:
                    psc = user_input.get(CONF_HDO_PSC, "").strip()
                    code_a = user_input.get(CONF_HDO_A, "").strip()
                    code_b = user_input.get(CONF_HDO_B, "").strip()
                    code_dp = user_input.get(CONF_HDO_DP, "").strip()
                    records = await client.async_match_classic(
                        psc, code_a, code_b, code_dp
                    )
                    self._options.update({
                        CONF_HDO_PSC: psc,
                        CONF_HDO_A: code_a,
                        CONF_HDO_B: code_b,
                        CONF_HDO_DP: code_dp,
                    })

                if not records:
                    errors["base"] = "hdo_not_found"
                else:
                    variants = HdoClient.variants(records)
                    if len(variants) > 1:
                        # Stejný kód může řídit víc relé (TAR, TUV) s jinými časy
                        self._hdo_records = records
                        return await self.async_step_hdo_varianta()
                    self._options[CONF_HDO_VARIANT] = variants[0].key
                    return await self.async_step_init()

            except HdoError as err:
                _LOGGER.warning("EGD: HDO – %s", err)
                errors["base"] = "hdo_error"

        mode = self._options.get(CONF_HDO_MODE, HDO_MODE_NONE)
        schema = vol.Schema(
            {
                vol.Required(CONF_HDO_MODE, default=mode): vol.In(
                    [HDO_MODE_NONE, HDO_MODE_SMART, HDO_MODE_CLASSIC]
                ),
                vol.Optional(
                    CONF_HDO_CODE,
                    description={
                        "suggested_value": self._options.get(CONF_HDO_CODE, "")
                    },
                ): str,
                vol.Optional(
                    CONF_HDO_PSC,
                    description={
                        "suggested_value": self._options.get(CONF_HDO_PSC, "")
                    },
                ): str,
                vol.Optional(
                    CONF_HDO_A,
                    description={"suggested_value": self._options.get(CONF_HDO_A, "")},
                ): str,
                vol.Optional(
                    CONF_HDO_B,
                    description={"suggested_value": self._options.get(CONF_HDO_B, "")},
                ): str,
                vol.Optional(
                    CONF_HDO_DP,
                    description={"suggested_value": self._options.get(CONF_HDO_DP, "")},
                ): str,
            }
        )
        return self.async_show_form(
            step_id="hdo", data_schema=schema, errors=errors
        )

    async def async_step_hdo_varianta(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Výběr relé, když kód odpovídá více rozvrhům."""
        variants = HdoClient.variants(self._hdo_records)

        if user_input is not None:
            self._options[CONF_HDO_VARIANT] = user_input[CONF_HDO_VARIANT]
            return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required(CONF_HDO_VARIANT): vol.In(
                    {v.key: v.label for v in variants}
                )
            }
        )
        return self.async_show_form(step_id="hdo_varianta", data_schema=schema)

    # ------------------------------------------------------------------
    # Cenová období
    # ------------------------------------------------------------------

    async def async_step_ceny(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Přehled cenových období."""
        return self.async_show_menu(
            step_id="ceny",
            menu_options=["cena_pridat", "cena_smazat", "init"],
            description_placeholders={
                "obdobi": "\n".join(f"• {p.label}" for p in self._prices)
                or "(zatím žádné)"
            },
        )

    async def async_step_cena_pridat(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Přidání cenového období."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                valid_from = date.fromisoformat(
                    user_input[KEY_VALID_FROM].strip()
                )
            except ValueError:
                errors[KEY_VALID_FROM] = "invalid_date"
            else:
                periods = [
                    p.as_dict()
                    for p in self._prices
                    if p.valid_from != valid_from  # stejné datum = přepis
                ]
                periods.append(
                    PricePeriod(
                        valid_from=valid_from,
                        price_vt=float(user_input[KEY_PRICE_VT]),
                        price_nt=float(
                            user_input.get(KEY_PRICE_NT, user_input[KEY_PRICE_VT])
                        ),
                        monthly_fee=float(user_input.get(KEY_MONTHLY_FEE, 0.0)),
                    ).as_dict()
                )
                self._options[CONF_PRICE_PERIODS] = sorted(
                    periods, key=lambda p: p[KEY_VALID_FROM]
                )
                return await self.async_step_ceny()

        schema = vol.Schema(
            {
                vol.Required(
                    KEY_VALID_FROM,
                    description={"suggested_value": date.today().replace(day=1).isoformat()},
                ): str,
                vol.Required(KEY_PRICE_VT): vol.Coerce(float),
                vol.Optional(KEY_PRICE_NT): vol.Coerce(float),
                vol.Optional(KEY_MONTHLY_FEE, default=0.0): vol.Coerce(float),
            }
        )
        return self.async_show_form(
            step_id="cena_pridat", data_schema=schema, errors=errors
        )

    async def async_step_cena_smazat(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Odebrání cenových období."""
        periods = list(self._prices)
        if not periods:
            return await self.async_step_ceny()

        if user_input is not None:
            keep = set(user_input.get("smazat", []))
            self._options[CONF_PRICE_PERIODS] = [
                p.as_dict()
                for p in periods
                if p.valid_from.isoformat() not in keep
            ]
            return await self.async_step_ceny()

        schema = vol.Schema(
            {
                vol.Optional("smazat", default=[]): cv.multi_select(
                    {p.valid_from.isoformat(): p.label for p in periods}
                )
            }
        )
        return self.async_show_form(step_id="cena_smazat", data_schema=schema)
