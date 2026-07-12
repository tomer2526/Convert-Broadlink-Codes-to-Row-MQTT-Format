"""Media player platform for SmartIR Native."""

from typing import Any

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
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
    """Set up one media player entity from its stored profile code."""
    data = await hass.async_add_executor_job(
        decode_profile_code, entry.data[CONF_PROFILE_CODE]
    )
    async_add_entities([SmartIrNativeMediaPlayer(entry, data)])


class SmartIrNativeMediaPlayer(SmartIrNativeReceiverEntity, MediaPlayerEntity):
    """A native Infrared media-player entity driven by SmartIR command data."""

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

        self._attr_unique_id = f"{entry.entry_id}_media_player"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_NAME],
            manufacturer=data.get("manufacturer"),
            model=", ".join(data.get("supportedModels", [])),
        )
        self._attr_state = MediaPlayerState.OFF
        self._attr_volume_level = 0.5
        self._attr_is_volume_muted = False
        self._attr_source = None
        if isinstance(sources := find_command_value(self._commands, "sources"), dict):
            self._attr_source_list = [str(source) for source in sources]
        self._attr_supported_features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.PLAY_MEDIA
            | MediaPlayerEntityFeature.NEXT_TRACK
            | MediaPlayerEntityFeature.PREVIOUS_TRACK
        )
        if self._attr_source_list:
            self._attr_supported_features |= MediaPlayerEntityFeature.SELECT_SOURCE
        self._set_receiver_commands(self._build_receiver_commands())

    async def async_turn_on(self) -> None:
        """Turn on the media player."""
        await self._send_named_command("on", "power_on", "power")
        self._attr_state = MediaPlayerState.ON
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        """Turn off the media player."""
        await self._send_named_command("off", "power_off", "power")
        self._attr_state = MediaPlayerState.OFF
        self.async_write_ha_state()

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute volume."""
        await self._send_named_command("mute")
        self._attr_is_volume_muted = mute
        self.async_write_ha_state()

    async def async_set_volume_level(self, volume: float) -> None:
        """Approximate set volume by stepping to the target level."""
        target = max(0.0, min(1.0, volume))
        step_command = "volume_up" if target >= self._attr_volume_level else "volume_down"
        aliases = (
            ("volume_up", "volumeUp")
            if step_command == "volume_up"
            else ("volume_down", "volumeDown")
        )
        if target == self._attr_volume_level:
            return
        await self._send_named_command(*aliases)
        self._attr_volume_level = target
        self.async_write_ha_state()

    async def async_volume_up(self) -> None:
        """Raise the volume."""
        await self._send_named_command("volume_up", "volumeUp")
        self._attr_volume_level = min(1.0, self._attr_volume_level + 0.05)
        self.async_write_ha_state()

    async def async_volume_down(self) -> None:
        """Lower the volume."""
        await self._send_named_command("volume_down", "volumeDown")
        self._attr_volume_level = max(0.0, self._attr_volume_level - 0.05)
        self.async_write_ha_state()

    async def async_media_play(self) -> None:
        """Send play command."""
        await self._send_named_command("play")
        self._attr_state = MediaPlayerState.PLAYING
        self.async_write_ha_state()

    async def async_media_pause(self) -> None:
        """Send pause command."""
        await self._send_named_command("pause")
        self._attr_state = MediaPlayerState.PAUSED
        self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        """Send stop command."""
        await self._send_named_command("stop")
        self._attr_state = MediaPlayerState.IDLE
        self.async_write_ha_state()

    async def async_media_play_pause(self) -> None:
        """Send play/pause toggle command."""
        await self._send_named_command("play_pause", "playPause")
        if self._attr_state == MediaPlayerState.PLAYING:
            self._attr_state = MediaPlayerState.PAUSED
        else:
            self._attr_state = MediaPlayerState.PLAYING
        self.async_write_ha_state()

    async def async_media_next_track(self) -> None:
        """Send next track or channel command."""
        await self._send_named_command("next_track", "nextTrack", "next_channel", "nextChannel")

    async def async_media_previous_track(self) -> None:
        """Send previous track or channel command."""
        await self._send_named_command(
            "previous_track",
            "previousTrack",
            "previous_channel",
            "previousChannel",
        )

    async def async_select_source(self, source: str) -> None:
        """Select source by SmartIR source name."""
        sources = find_command_value(self._commands, "sources")
        if not isinstance(sources, dict):
            raise HomeAssistantError("SmartIR media profile does not define sources")
        command = find_command_value(sources, source)
        if command is None:
            raise HomeAssistantError(f"Unknown source '{source}'")
        await self._send_value(command)
        self._attr_source = source
        self.async_write_ha_state()

    async def async_play_media(
        self,
        media_type: str,
        media_id: str,
        **kwargs: Any,
    ) -> None:
        """Play a source or channel from media payload."""
        del kwargs
        if media_type == "source":
            await self.async_select_source(media_id)
            return
        raise HomeAssistantError("Only media_type='source' is supported")

    async def _send_named_command(self, *aliases: str) -> None:
        """Find and send a named command from the profile."""
        command = find_command_value(self._commands, *aliases)
        if command is None:
            raise HomeAssistantError(
                f"SmartIR media profile does not define command aliases: {', '.join(aliases)}"
            )
        await self._send_value(command)

    def _build_receiver_commands(self) -> dict[str, Any]:
        """Build command-key mapping for receiver signal matching."""
        receiver_commands: dict[str, Any] = {}
        for key, aliases in (
            ("on", ("on", "power_on", "power")),
            ("off", ("off", "power_off", "power")),
            ("mute", ("mute",)),
            ("volume_up", ("volume_up", "volumeUp")),
            ("volume_down", ("volume_down", "volumeDown")),
            ("play", ("play",)),
            ("pause", ("pause",)),
            ("stop", ("stop",)),
            ("play_pause", ("play_pause", "playPause")),
            ("next", ("next_track", "nextTrack", "next_channel", "nextChannel")),
            (
                "previous",
                (
                    "previous_track",
                    "previousTrack",
                    "previous_channel",
                    "previousChannel",
                ),
            ),
        ):
            if command := find_command_value(self._commands, *aliases):
                receiver_commands[key] = command
        if isinstance(sources := find_command_value(self._commands, "sources"), dict):
            for source, command in sources.items():
                receiver_commands[f"source:{source}"] = command
        return receiver_commands

    def _handle_matched_receiver_command(self, key: str) -> bool:
        """Update media-player state from one matched receiver command."""
        old_state = (
            self._attr_state,
            self._attr_volume_level,
            self._attr_is_volume_muted,
            self._attr_source,
        )
        if key == "on":
            self._attr_state = MediaPlayerState.ON
        elif key == "off":
            self._attr_state = MediaPlayerState.OFF
        elif key == "mute":
            self._attr_is_volume_muted = not self._attr_is_volume_muted
        elif key == "volume_up":
            self._attr_volume_level = min(1.0, self._attr_volume_level + 0.05)
        elif key == "volume_down":
            self._attr_volume_level = max(0.0, self._attr_volume_level - 0.05)
        elif key == "play":
            self._attr_state = MediaPlayerState.PLAYING
        elif key == "pause":
            self._attr_state = MediaPlayerState.PAUSED
        elif key == "stop":
            self._attr_state = MediaPlayerState.IDLE
        elif key == "play_pause":
            if self._attr_state == MediaPlayerState.PLAYING:
                self._attr_state = MediaPlayerState.PAUSED
            else:
                self._attr_state = MediaPlayerState.PLAYING
        elif key.startswith("source:"):
            self._attr_source = key.split(":", 1)[1]
            self._attr_state = MediaPlayerState.ON
        return old_state != (
            self._attr_state,
            self._attr_volume_level,
            self._attr_is_volume_muted,
            self._attr_source,
        )
