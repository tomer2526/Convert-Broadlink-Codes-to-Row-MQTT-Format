# SmartIR and Home Assistant IR Converter

A tiny, browser-only tool for converting IR command payloads for SmartIR and Home Assistant native Infrared.

It supports:
- Broadlink Base64 to Raw MQTT
- Raw MQTT to Broadlink Base64
- Broadlink Base64 to Home Assistant signed raw timings
- Raw MQTT to Home Assistant signed raw timings
- Home Assistant signed raw timings to Raw MQTT
- Home Assistant signed raw timings to Broadlink Base64

No uploads, no Python, everything runs locally.

The converter can process a full SmartIR JSON file or a single pasted IR code.

It can also turn a SmartIR JSON file into a compact `IRP1:` profile code for the reusable SmartIR Native custom integration. Supported device types are climate, fan, light, and TV/media_player. Each profile creates a native Home Assistant entity and sends commands through a Home Assistant Infrared emitter entity. If the configured emitter is unavailable, the entity is marked unavailable too.

## SmartIR Native installation

SmartIR Native requires Home Assistant 2026.6 or newer.

- [SmartIR Native integration source](https://github.com/tomer2526/Convert-Broadlink-Codes-to-Row-MQTT-Format/tree/main/custom_components/smartir_native)

1. In HACS, add this repository as a custom Integration repository, or use the **Install with HACS** button on the website.
2. Download SmartIR Native in HACS and restart Home Assistant.
3. On the website, open **Native HA integration**, upload a SmartIR climate/fan/light/TV JSON file, and create a profile code.
4. Copy the profile code and select **Add to Home Assistant**.
5. Paste the code, choose the Infrared emitter, and name the entity.
6. For climate profiles, you can optionally choose an Infrared receiver. When a known command is received from the physical remote, the climate entity updates its HVAC mode, target temperature, fan mode, and swing mode.

Install the integration only once. Repeat steps 3-6 for every additional IR device. A receiver is optional and only applies to climate profiles; without one, the entity continues to work in transmit-only assumed-state mode. Use the integration's **Configure** button to change the emitter and, for climate entries, add or replace a receiver. The **Reconfigure** menu can also edit these fields together with the device name. Profile creation remains entirely local in the browser.

[Try it!](https://tomer2526.github.io/Convert-Broadlink-Codes-to-Row-MQTT-Format/)

## SmartIR Guide
- [Zigbee SmartIR guide](https://community.home-assistant.io/t/guide-how-to-use-the-zs06-or-ufo-r11-zigbee-ir-controllers-with-smartir/939301?u=tomer11)

## SmartIR Project
- [SmartIR GitHub repository](https://github.com/smartHomeHub/SmartIR/tree/master?tab=readme-ov-file)

## Home Assistant Infrared
- [Home Assistant infrared entity documentation](https://developers.home-assistant.io/docs/core/entity/infrared/)
- [Zigbee IR bridge for native Home Assistant infrared](https://github.com/tomer2526/IR-Wrapper-for-Zigbee-IR-Bluster/tree/main/custom_components/z2m_ir_bridge)
