"""Climate platform for SmartIR Native."""

import asyncio
import logging
from typing import Any

from infrared_protocols.commands import Command

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode
from homeassistant.components.infrared import (
    InfraredEmitterConsumerEntity,
    InfraredReceivedSignal,
    async_subscribe_receiver,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, UnitOfTemperature
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_INFRARED_ENTITY_ID,
    CONF_INFRARED_RECEIVER_ENTITY_ID,
    CONF_NAME,
    CONF_PROFILE_CODE,
    DOMAIN,
)
from .profile import decode_profile_code
from .receiver import ON_STATE, build_command_states, find_command_state

_LOGGER = logging.getLogger(__name__)


class StoredRawCommand(Command):
    """An IR command backed by signed microsecond timings."""

    def __init__(self, timings: list[int]) -> None:
        """Initialize a 38 kHz raw timing command."""
        super().__init__(modulation=38000)
        self._timings = timings

    def get_raw_timings(self) -> list[int]:
        """Return alternating positive pulse and negative space timings."""
        return self._timings


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one climate entity from its stored profile code."""
    data = await hass.async_add_executor_job(
        decode_profile_code, entry.data[CONF_PROFILE_CODE]
    )
    async_add_entities([SmartIrNativeClimate(entry, data)])


class SmartIrNativeClimate(
    InfraredEmitterConsumerEntity, ClimateEntity, RestoreEntity
):
    """A native Infrared climate entity driven by SmartIR command data."""

    _attr_assumed_state = True
    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, entry: ConfigEntry, data: dict[str, Any]) -> None:
        """Initialize the entity from validated profile data."""
        self._infrared_emitter_entity_id = entry.options.get(
            CONF_INFRARED_ENTITY_ID,
            entry.data[CONF_INFRARED_ENTITY_ID],
        )
        self._infrared_receiver_entity_id = entry.options.get(
            CONF_INFRARED_RECEIVER_ENTITY_ID,
            entry.data.get(CONF_INFRARED_RECEIVER_ENTITY_ID),
        )
        self._commands = data["commands"]
        self._received_command_states = build_command_states(data)
        self._remove_receiver_subscription: CALLBACK_TYPE | None = None
        self._send_lock = asyncio.Lock()
        self._last_on_mode: HVACMode | None = None

        self._attr_unique_id = f"{entry.entry_id}_climate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_NAME],
            manufacturer=data.get("manufacturer"),
            model=", ".join(data.get("supportedModels", [])),
        )
        self._attr_min_temp = data["minTemperature"]
        self._attr_max_temp = data["maxTemperature"]
        self._attr_target_temperature_step = data.get("precision", 1)
        self._attr_target_temperature = self._attr_min_temp
        self._attr_hvac_modes = [HVACMode.OFF] + [
            HVACMode(mode) for mode in data["operationModes"]
        ]
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_fan_modes = data["fanModes"]
        self._attr_fan_mode = self._attr_fan_modes[0]
        self._attr_swing_modes = data.get("swingModes")
        self._attr_swing_mode = (
            self._attr_swing_modes[0] if self._attr_swing_modes else None
        )
        self._attr_supported_features = (
            ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
        )
        if self._attr_swing_modes:
            self._attr_supported_features |= ClimateEntityFeature.SWING_MODE

    async def async_added_to_hass(self) -> None:
        """Restore state and subscribe to the optional Infrared receiver."""
        await super().async_added_to_hass()
        if self._infrared_receiver_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [self._infrared_receiver_entity_id],
                    self._receiver_state_changed,
                )
            )
            self.async_on_remove(self._unsubscribe_receiver)
            receiver_state = self.hass.states.get(
                self._infrared_receiver_entity_id
            )
            if receiver_state is not None and receiver_state.state != STATE_UNAVAILABLE:
                self._subscribe_receiver()
        if (state := await self.async_get_last_state()) is None:
            return
        try:
            self._attr_hvac_mode = HVACMode(state.state)
        except ValueError:
            self._attr_hvac_mode = HVACMode.OFF
        if (temperature := state.attributes.get("temperature")) is not None:
            self._attr_target_temperature = temperature
        if (fan_mode := state.attributes.get("fan_mode")) in self._attr_fan_modes:
            self._attr_fan_mode = fan_mode
        if self._attr_swing_modes:
            swing_mode = state.attributes.get("swing_mode")
            if swing_mode in self._attr_swing_modes:
                self._attr_swing_mode = swing_mode
        if self._attr_hvac_mode != HVACMode.OFF:
            self._last_on_mode = self._attr_hvac_mode

    @callback
    def _receiver_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Follow receiver reloads without changing climate availability."""
        new_state = event.data["new_state"]
        if new_state is None or new_state.state == STATE_UNAVAILABLE:
            self._unsubscribe_receiver()
        else:
            self._subscribe_receiver()

    @callback
    def _subscribe_receiver(self) -> None:
        """Subscribe when the optional receiver is available."""
        if (
            not self._infrared_receiver_entity_id
            or self._remove_receiver_subscription is not None
        ):
            return
        try:
            self._remove_receiver_subscription = async_subscribe_receiver(
                self.hass,
                self._infrared_receiver_entity_id,
                self._handle_received_signal,
            )
        except HomeAssistantError:
            _LOGGER.warning(
                "Unable to subscribe to Infrared receiver %s",
                self._infrared_receiver_entity_id,
            )

    @callback
    def _unsubscribe_receiver(self) -> None:
        """Remove the optional receiver subscription."""
        if self._remove_receiver_subscription is None:
            return
        self._remove_receiver_subscription()
        self._remove_receiver_subscription = None

    @callback
    def _handle_received_signal(self, signal: InfraredReceivedSignal) -> None:
        """Update assumed climate state when a known remote command is received."""
        matched = find_command_state(
            signal.timings,
            self._received_command_states,
            current_hvac_mode=self._attr_hvac_mode.value,
            current_fan_mode=self._attr_fan_mode,
            current_swing_mode=self._attr_swing_mode,
            current_temperature=self._attr_target_temperature,
        )
        if matched is None:
            return

        if matched.hvac_mode == HVACMode.OFF.value:
            if self._attr_hvac_mode != HVACMode.OFF:
                self._last_on_mode = self._attr_hvac_mode
            self._attr_hvac_mode = HVACMode.OFF
        elif matched.hvac_mode == ON_STATE:
            self._attr_hvac_mode = (
                self._last_on_mode or self._attr_hvac_modes[1]
            )
            self._last_on_mode = self._attr_hvac_mode
        else:
            self._attr_hvac_mode = HVACMode(matched.hvac_mode)
            self._last_on_mode = self._attr_hvac_mode
            if matched.fan_mode is not None:
                self._attr_fan_mode = matched.fan_mode
            if matched.swing_mode is not None:
                self._attr_swing_mode = matched.swing_mode
            if matched.temperature is not None:
                self._attr_target_temperature = matched.temperature
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature and optionally the HVAC mode."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            temperature = min(self.max_temp, max(self.min_temp, temperature))
            step = self.target_temperature_step or 1
            self._attr_target_temperature = round(temperature / step) * step

        requested_mode = kwargs.get("hvac_mode")
        if requested_mode is not None:
            self._attr_hvac_mode = HVACMode(requested_mode)
            if self._attr_hvac_mode != HVACMode.OFF:
                self._last_on_mode = self._attr_hvac_mode
        if self._attr_hvac_mode != HVACMode.OFF or requested_mode is not None:
            await self._send_current_command()
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the HVAC mode and transmit its complete state."""
        self._attr_hvac_mode = HVACMode(hvac_mode)
        if self._attr_hvac_mode != HVACMode.OFF:
            self._last_on_mode = self._attr_hvac_mode
        await self._send_current_command()
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan mode."""
        self._attr_fan_mode = fan_mode
        if self._attr_hvac_mode != HVACMode.OFF:
            await self._send_current_command()
        self.async_write_ha_state()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set the swing mode."""
        self._attr_swing_mode = swing_mode
        if self._attr_hvac_mode != HVACMode.OFF:
            await self._send_current_command()
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Turn on using the last active or first supported mode."""
        await self.async_set_hvac_mode(
            self._last_on_mode or self._attr_hvac_modes[1]
        )

    async def async_turn_off(self) -> None:
        """Turn off the climate device."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def _send_current_command(self) -> None:
        """Resolve and send the command for the complete current state."""
        async with self._send_lock:
            if self._attr_hvac_mode == HVACMode.OFF:
                await self._send_value(self._commands["off"])
                return
            if "on" in self._commands:
                await self._send_value(self._commands["on"])
                await asyncio.sleep(0.5)
            command = self._commands[self._attr_hvac_mode.value][self._attr_fan_mode]
            if self._attr_swing_modes:
                command = command[self._attr_swing_mode]
            temperature = f"{self._attr_target_temperature:g}"
            await self._send_value(command[temperature])

    async def _send_value(self, value: Any) -> None:
        """Send one timing array or a sequence of timing arrays."""
        commands = [value] if value and isinstance(value[0], int) else value
        for timings in commands:
            await self._send_command(StoredRawCommand(timings))
