"""Platform for TechLife Pro light integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
import homeassistant.util.color as color_util
from homeassistant.util import slugify
from datetime import timedelta

from .const import DOMAIN, REFRESH_INTERVAL_SECONDS
from .protocol import (
    LIGHT_TYPE_RGB,
    LIGHT_TYPE_WHITE,
    TechLifeProtocol,
    TechLifeState,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the TechLife Pro light platform."""
    discovered_macs: set[str] = set()

    @callback
    def message_received(msg) -> None:
        topic = msg.topic
        try:
            mac = topic.split("_")[-1]
        except IndexError:
            return

        if not mac or mac in discovered_macs:
            return

        _LOGGER.info("Discovered TechLife Pro device: %s", mac)
        discovered_macs.add(mac)
        async_add_entities([TechLifeProLight(mac)])

    await mqtt.async_subscribe(hass, "dev_pub_+", message_received)


class TechLifeProLight(LightEntity):
    """Representation of a TechLife Pro Light."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, mac: str) -> None:
        self._mac = mac
        self._attr_unique_id = slugify(f"techlife_{mac}")
        self._attr_name = f"TechLife Strip {mac}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            name=f"TechLife Strip {mac}",
            manufacturer="TechLife",
            model="Pro LED Strip",
        )
        self._cmd_topic = f"dev_sub_{mac}"
        self._state_topic = f"dev_pub_{mac}"

        self._is_on: bool | None = False
        self._available: bool = False
        self._brightness: int = 255
        self._hs_color: tuple[float, float] | None = None
        self._light_type: str | None = None

    @property
    def is_on(self) -> bool | None:
        return self._is_on

    @property
    def available(self) -> bool:
        return self._available

    @property
    def brightness(self) -> int | None:
        return self._brightness

    @property
    def hs_color(self) -> tuple[float, float] | None:
        return self._hs_color if self._light_type == LIGHT_TYPE_RGB else None

    @property
    def color_mode(self) -> ColorMode:
        if self._light_type == LIGHT_TYPE_RGB:
            return ColorMode.HS
        if self._light_type == LIGHT_TYPE_WHITE:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        if self._light_type == LIGHT_TYPE_RGB:
            return {ColorMode.HS, ColorMode.BRIGHTNESS}
        if self._light_type == LIGHT_TYPE_WHITE:
            return {ColorMode.BRIGHTNESS}
        return {ColorMode.ONOFF}

    async def async_added_to_hass(self) -> None:
        await mqtt.async_subscribe(
            self.hass, self._state_topic, self._handle_state_message, encoding=None
        )
        await self._async_request_refresh()
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_periodic_refresh,
                timedelta(seconds=REFRESH_INTERVAL_SECONDS),
            )
        )

    @callback
    def _handle_state_message(self, msg) -> None:
        payload = msg.payload
        if isinstance(payload, str):
            try:
                payload = bytes.fromhex(payload)
            except ValueError:
                payload = payload.encode("latin-1", errors="ignore")

        state: TechLifeState | None = TechLifeProtocol.parse_status(payload)
        if state is None:
            return

        if state.light_type is not None:
            self._light_type = state.light_type
        if state.is_available is not None:
            self._available = state.is_available
        if state.is_on is not None:
            self._is_on = state.is_on

        if state.light_type == LIGHT_TYPE_RGB:
            if state.rgb is not None:
                self._hs_color = color_util.color_RGB_to_hs(*state.rgb)
            if state.brightness is not None:
                self._brightness = state.brightness
        elif state.light_type == LIGHT_TYPE_WHITE:
            if state.brightness_white is not None:
                self._brightness = state.brightness_white

        self.async_write_ha_state()

    async def _async_periodic_refresh(self, _now=None) -> None:
        await self._async_request_refresh()

    async def _async_request_refresh(self) -> None:
        await self._async_publish(TechLifeProtocol.get_refresh_command())

    async def _async_publish(self, payload: bytes) -> None:
        await mqtt.async_publish(
            self.hass, self._cmd_topic, payload, retain=False, encoding=None
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        wants_color = ATTR_HS_COLOR in kwargs
        wants_brightness = ATTR_BRIGHTNESS in kwargs

        if wants_brightness:
            self._brightness = int(kwargs[ATTR_BRIGHTNESS])
        if wants_color:
            self._hs_color = kwargs[ATTR_HS_COLOR]

        if not self._is_on and not (wants_color or wants_brightness):
            await self._async_publish(TechLifeProtocol.get_on_command())
            self._is_on = True
            self.async_write_ha_state()
            await self._async_request_refresh()
            return

        if self._light_type == LIGHT_TYPE_RGB:
            hs = self._hs_color or (0.0, 0.0)
            r, g, b = color_util.color_hs_to_RGB(hs[0], hs[1])
            cmd = TechLifeProtocol.get_rgb_command(r, g, b, self._brightness)
        elif self._light_type == LIGHT_TYPE_WHITE:
            cmd = TechLifeProtocol.get_white_command(self._brightness)
        else:
            cmd = TechLifeProtocol.get_on_command()

        await self._async_publish(cmd)
        self._is_on = True
        self.async_write_ha_state()
        await self._async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_publish(TechLifeProtocol.get_off_command())
        self._is_on = False
        self.async_write_ha_state()
        await self._async_request_refresh()
