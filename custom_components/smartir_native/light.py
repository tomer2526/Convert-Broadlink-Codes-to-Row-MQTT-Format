"""Light platform for SmartIR Native."""

import asyncio
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_INFRARED_ENTITY_ID, CONF_NAME, CONF_PROFILE_CODE, DOMAIN
from .emitter_base import SmartIrNativeEmitterEntity, find_command_value
from .profile import decode_profile_code


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one light entity from its stored profile code."""
    data = await hass.async_add_executor_job(
        decode_profile_code, entry.data[CONF_PROFILE_CODE]
    )
    async_add_entities([SmartIrNativeLight(entry, data)])


class SmartIrNativeLight(SmartIrNativeEmitterEntity, LightEntity):
    """A native Infrared light entity driven by SmartIR command data."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, data: dict[str, Any]) -> None:
        """Initialize the entity from validated profile data."""
        super().__init__(
            entry.options.get(
                CONF_INFRARED_ENTITY_ID,
                entry.data[CONF_INFRARED_ENTITY_ID],
            )
        )
        self._commands = data["commands"]
        self._brightness_levels = [
            int(level)
            for level in data.get("brightness", [])
            if isinstance(level, int | float) and not isinstance(level, bool)
        ]
        self._color_temperatures = [
            int(level)
            for level in data.get("colorTemperature", [])
            if isinstance(level, int | float) and not isinstance(level, bool)
        ]
        self._send_lock = asyncio.Lock()

        self._attr_unique_id = f"{entry.entry_id}_light"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_NAME],
            manufacturer=data.get("manufacturer"),
            model=", ".join(data.get("supportedModels", [])),
        )
        self._attr_is_on = False
        self._attr_brightness = (
            self._brightness_levels[-1] if self._brightness_levels else None
        )
        self._attr_color_temp_kelvin = (
            self._color_temperatures[0] if self._color_temperatures else None
        )
        if self._brightness_levels:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light and optionally apply a brightness/temperature command."""
        async with self._send_lock:
            command = None
            if ATTR_COLOR_TEMP_KELVIN in kwargs:
                command = self._resolve_color_temperature_command(
                    int(kwargs[ATTR_COLOR_TEMP_KELVIN])
                )
                self._attr_color_temp_kelvin = int(kwargs[ATTR_COLOR_TEMP_KELVIN])
            if command is None and ATTR_BRIGHTNESS in kwargs:
                command = self._resolve_brightness_command(int(kwargs[ATTR_BRIGHTNESS]))
                self._attr_brightness = int(kwargs[ATTR_BRIGHTNESS])
            if command is None:
                command = find_command_value(self._commands, "on", "power_on", "power")
            if command is None:
                if self._brightness_levels:
                    command = self._resolve_brightness_command(
                        self._attr_brightness or self._brightness_levels[-1]
                    )
                elif self._color_temperatures:
                    command = self._resolve_color_temperature_command(
                        self._attr_color_temp_kelvin or self._color_temperatures[0]
                    )
            if command is None:
                raise HomeAssistantError("SmartIR light profile has no usable turn-on command")
            await self._send_value(command)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        del kwargs
        command = find_command_value(self._commands, "off", "power_off", "power")
        if command is None:
            raise HomeAssistantError("SmartIR light profile does not define an off command")
        await self._send_value(command)
        self._attr_is_on = False
        self.async_write_ha_state()

    def _resolve_brightness_command(self, brightness: int) -> Any:
        """Resolve nearest SmartIR brightness command."""
        brightness_commands = find_command_value(self._commands, "brightness")
        if not isinstance(brightness_commands, dict) or not self._brightness_levels:
            return None
        bounded = max(0, min(255, brightness))
        nearest = min(self._brightness_levels, key=lambda level: abs(level - bounded))
        return find_command_value(brightness_commands, str(nearest), nearest)

    def _resolve_color_temperature_command(self, kelvin: int) -> Any:
        """Resolve nearest SmartIR color-temperature command."""
        color_temperature_commands = find_command_value(
            self._commands,
            "colorTemperature",
            "color_temperature",
        )
        if (
            not isinstance(color_temperature_commands, dict)
            or not self._color_temperatures
        ):
            return None
        nearest = min(self._color_temperatures, key=lambda level: abs(level - kelvin))
        return find_command_value(color_temperature_commands, str(nearest), nearest)
