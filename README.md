# Secure / Horstmann E7+ immersion heater for Home Assistant

A custom Home Assistant integration that monitors and controls a **Secure (Horstmann) E7+ (SCE100)** immersion-heater controller over Bluetooth Low Energy.

These controllers are sold with UK Economy 7 and flexible-tariff immersion setups. They are normally only controllable from the vendor's phone app, over a proprietary, authenticated BLE protocol, with no documented API and no local integration. This project reverse-engineers that protocol so the heater can be driven from Home Assistant: scheduled around cheap-rate windows, boosted on demand, and monitored for real metered energy use.

> Built against a real device. It is shared as-is for other tinkerers, not as an officially supported integration.

## What it does

- **On/off and boost control**, with selectable boost durations
- **Follow-schedule toggle**, so the device can run its own weekly programme or be driven by Home Assistant
- **Live state**: current mode, boost remaining, and the next scheduled change
- **Real metered energy**, read from the device's own consumption store (daily kWh, split into boost vs off-peak), not estimated from on-time
- **Automatic key retrieval**: no app decompiling or manual key entry. Put the unit in commissioning mode and the integration claims it and pulls its BLE key itself

## How it works

The E7+ speaks a Nordic UART BLE service carrying a JSON-RPC protocol wrapped in a four-pass AES-128 challenge-response handshake, with a length-prefixed, chunked framing layer on top. The integration:

1. Connects over Home Assistant's Bluetooth stack, so a local adapter or any **ESPHome Bluetooth proxy** works transparently.
2. Performs the four-pass authentication using the device's 16-byte key.
3. Polls `GetAllServiceValues` for live state, and the `DynamicTariff` consumption handler for metered energy.
4. Sends boost, power and schedule commands as authenticated writes.

All of the BLE transport, framing and crypto lives in `custom_components/secure_e7/coordinator.py`. The commissioning (key-retrieval) flow is in `commission.py`.

## Requirements

- Home Assistant 2024.1 or newer
- A way for Home Assistant to reach the heater over BLE:
  - Bluetooth built into the HA host (for example HA Green/Yellow, a Raspberry Pi, or a USB adapter) **in range of the heater**, or
  - an **ESPHome Bluetooth proxy** (for example a cheap ESP32) in range. This is the usual answer when the HA host has no Bluetooth or is not close enough to the unit.

## Installation

### HACS (custom repository)

1. In HACS, open the menu, choose **Custom repositories**, and add `https://github.com/isaacrushworth2002/ha-secure-e7` with type *Integration*.
2. Install **Secure / Horstmann E7+ Immersion** and restart Home Assistant.

### Manual

Copy `custom_components/secure_e7` into your Home Assistant `config/custom_components/` directory and restart.

## Setup

1. Go to **Settings > Devices & Services > Add Integration** and search for **Secure / Horstmann E7+**.
2. The heater should be discovered over Bluetooth. If not, enter its MAC address manually.
3. Choose how to get the key:
   - **Pull the key automatically (recommended)**: on the unit, hold the Menu button for about 8 seconds until the display shows commissioning mode, then continue. The integration claims the device and reads its key. Note that this re-keys the device, so the vendor phone app will no longer control it.
   - **I already have my key**: paste a known 32-character hex key.

The heater then appears as a single device with its control and sensor entities grouped under it.

## Status and limitations

- **Weekly schedule**: `secure_e7.get_schedule` reads the device's weekly programme into the **Schedule** sensor (state: today's ON windows; attributes: the whole week); it is also re-read every 30 minutes, so console edits show up. `secure_e7.set_schedule` writes it: give each day to change as a list of `{on, off}` windows (for example `monday: [{"on": "02:15", "off": "06:00"}]`, or `[]` for off all day; quote `"on"` and `"off"` in YAML, or they become true/false). Days left out are not touched. The device limits are enforced before anything is sent: at most 3 ON windows a day, on the 15-minute grid, no overlaps. After writing, the programme is read back and the action fails if it does not match. **Next Change Time** is worked out from the programme (blank when follow-schedule is off). There is no sync with Home Assistant's Schedule helper.
- **Bluetooth reliability**: an ESPHome proxy stops scanning while it tries to connect, so connection retries are kept short and, after three failed polls in a row, polling backs off to 2, then 5, then 10 minutes, returning to every minute on the first success. If the heater stays unavailable and the proxy reports no Bluetooth devices at all, restart the proxy.
- Metered energy is accumulated amp-hours converted at a nominal 230 V, exactly as the vendor app does. It is metered, not a guess, but it is not a true power measurement.
- Tested against a single HardwareType 65 (`E7Plus_2_Channel`) unit. Other variants may differ.

## Safety and legal note

This talks to your own hardware on your own network. Commissioning re-keys the device and takes ownership away from the vendor app. Use at your own risk. Not affiliated with or endorsed by Secure Controls or Horstmann.

## Licence

MIT. See [LICENSE](LICENSE).
