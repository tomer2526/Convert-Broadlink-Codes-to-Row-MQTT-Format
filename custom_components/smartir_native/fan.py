"""Fan platform for SmartIR Native."""

import asyncio
from typing import Any

from homeassistant.components.fan import (
    DIRECTION_FORWARD,
    DIRECTION_REVERSE,
    FanEntity,
    FanEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_INFRARED_ENTITY_ID,
    CONF_INFRARED_RECEIVER_ENTITY_ID,
    CONF_NAME,
    CONF_PROFILE_CODE,
    DOMAIN,
)
from .emitter_base import SmartIrNativeReceiverEntity, find_command_value
from .profile import decode_profile_code


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one fan entity from its stored profile code."""
    data = await hass.async_add_executor_job(
        decode_profile_code, entry.data[CONF_PROFILE_CODE]
    )
    async_add_entities([SmartIrNativeFan(entry, data)])


class SmartIrNativeFan(SmartIrNativeReceiverEntity, FanEntity):
    """A native Infrared fan entity driven by SmartIR command data."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, data: dict[str, Any]) -> None:
        """Initialize the entity from validated profile data."""
        super().__init__(
            entry.options.get(
                CONF_INFRARED_ENTITY_ID,
                entry.data[CONF_INFRARED_ENTITY_ID],
            ),
            entry.options.get(
                CONF_INFRARED_RECEIVER_ENTITY_ID,
                entry.data.get(CONF_INFRARED_RECEIVER_ENTITY_ID),
            ),
        )
        self._commands = data["commands"]
        self._speed_steps = [str(item) for item in data["speed"]]
        self._send_lock = asyncio.Lock()

        self._attr_unique_id = f"{entry.entry_id}_fan"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_NAME],
            manufacturer=data.get("manufacturer"),
            model=", ".join(data.get("supportedModels", [])),
        )
        self._attr_supported_features = (
            FanEntityFeature.TURN_ON
            | FanEntityFeature.TURN_OFF
            | FanEntityFeature.PRESET_MODE
            | FanEntityFeature.DIRECTION
        )
        self._attr_preset_modes = self._speed_steps
        self._attr_preset_mode = self._speed_steps[0]
        self._attr_direction = (
            DIRECTION_FORWARD
            if "forward" in self._commands
            else (
                DIRECTION_REVERSE
                if "reverse" in self._commands
                else DIRECTION_FORWARD
            )
        )
        self._attr_is_on = False
        self._set_receiver_commands(self._build_receiver_commands())

    async def async_turn_on(
        self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any
    ) -> None:
        """Turn on the fan."""
        if direction := kwargs.get("direction"):
            await self.async_set_direction(direction)
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
            return
        if percentage is not None:
            await self.async_set_percentage(percentage)
            return
        self._attr_is_on = True
        if command := find_command_value(self._commands, "on", "power", "power_on"):
            await self._send_value(command)
        else:
            await self._send_speed_command(self._attr_preset_mode)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        del kwargs
        command = find_command_value(self._commands, "off", "power_off", "power")
        if command is None:
            raise HomeAssistantError("SmartIR fan profile does not define an off command")
        await self._send_value(command)
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set fan speed by SmartIR preset label."""
        if preset_mode not in self._speed_steps:
            raise HomeAssistantError("Unsupported fan speed preset")
        await self._send_speed_command(preset_mode)
        self._attr_preset_mode = preset_mode
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        """Set fan speed by percentage."""
        if percentage <= 0:
            await self.async_turn_off()
            return
        speed_index = round((len(self._speed_steps) - 1) * (percentage / 100))
        speed = self._speed_steps[max(0, min(len(self._speed_steps) - 1, speed_index))]
        await self.async_set_preset_mode(speed)

    async def async_set_direction(self, direction: str) -> None:
        """Set fan rotation direction."""
        if direction not in (DIRECTION_FORWARD, DIRECTION_REVERSE):
            raise HomeAssistantError("Unsupported fan direction")
        self._attr_direction = direction
        if self._attr_is_on:
            await self._send_speed_command(self._attr_preset_mode)
        self.async_write_ha_state()

    async def _send_speed_command(self, speed: str) -> None:
        """Send a speed command for the current direction."""
        async with self._send_lock:
            command_tree: Any = self._commands
            if isinstance(command_tree, dict):
                direction_node = find_command_value(
                    command_tree,
                    "forward" if self._attr_direction == DIRECTION_FORWARD else "reverse",
                )
                if isinstance(direction_node, dict):
                    command_tree = direction_node
            command = find_command_value(command_tree, speed)
            if command is None:
                raise HomeAssistantError(
                    f"SmartIR fan profile has no command for speed '{speed}'"
                )
            await self._send_value(command)

    def _build_receiver_commands(self) -> dict[str, Any]:
        """Build command-key mapping for receiver signal matching."""
        receiver_commands: dict[str, Any] = {}
        if off := find_command_value(self._commands, "off", "power_off", "power"):
            receiver_commands["off"] = off
        if on := find_command_value(self._commands, "on", "power_on", "power"):
            receiver_commands["on"] = on
        for direction_key, direction in (
            ("forward", DIRECTION_FORWARD),
            ("reverse", DIRECTION_REVERSE),
        ):
            direction_commands = find_command_value(self._commands, direction_key)
            if not isinstance(direction_commands, dict):
                continue
            for speed in self._speed_steps:
                if command := find_command_value(direction_commands, speed):
                    receiver_commands[f"{direction}:{speed}"] = command
        for speed in self._speed_steps:
            if command := find_command_value(self._commands, speed):
                receiver_commands[f"speed:{speed}"] = command
        return receiver_commands

    def _handle_matched_receiver_command(self, key: str) -> bool:
        """Update fan state from one matched receiver command."""
        old_state = (self._attr_is_on, self._attr_preset_mode, self._attr_direction)
        if key == "off":
            self._attr_is_on = False
        elif key == "on":
            self._attr_is_on = True
        elif key.startswith("forward:"):
            self._attr_direction = DIRECTION_FORWARD
            self._attr_preset_mode = key.split(":", 1)[1]
            self._attr_is_on = True
        elif key.startswith("reverse:"):
            self._attr_direction = DIRECTION_REVERSE
            self._attr_preset_mode = key.split(":", 1)[1]
            self._attr_is_on = True
        elif key.startswith("speed:"):
            self._attr_preset_mode = key.split(":", 1)[1]
            self._attr_is_on = True
        return old_state != (
            self._attr_is_on,
            self._attr_preset_mode,
            self._attr_direction,
        )
