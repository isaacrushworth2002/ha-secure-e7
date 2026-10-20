# Secure / Horstmann E7+ immersion heater for Home Assistant

Control the Secure (Horstmann) E7+ immersion heater controller (SCE100) from Home Assistant over Bluetooth, instead of the Secure phone app.

Handy if you're on Economy 7 or a flexible tariff and want to heat water in the cheap slots, boost it from an automation, or see how much energy it's using.

**Before you start:** setting it up claims the controller for Home Assistant, and the Secure phone app stops working with it.

## What you get

- Hot water on/off, and boost with a choice of 30, 60 or 120 minutes
- A switch for whether the unit follows its own weekly schedule
- The weekly schedule itself, which you can read and change from Home Assistant
- Current mode, boost time left, and when the next scheduled change is
- Daily energy use from the unit's own meter, split into boost and off-peak

## What you need

- Home Assistant 2024.1 or newer
- Bluetooth that can reach the heater. Either the Home Assistant machine has Bluetooth and is close enough, or you have an [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html) near the heater. A cheap ESP32 does the job and is the usual answer.

## Install

With HACS:

1. In HACS, open the menu, choose **Custom repositories**, add `https://github.com/isaacrushworth2002/ha-secure-e7` and pick type *Integration*.
2. Install **Secure / Horstmann E7+ Immersion** and restart Home Assistant.

Or copy `custom_components/secure_e7` into your `config/custom_components/` folder and restart.

## Set up

1. Go to **Settings > Devices & services > Add integration** and search for **Secure / Horstmann E7+**. It may already be waiting under "Discovered".
2. Choose **Pull the key automatically**.
3. On the heater, hold the Menu button for about 8 seconds until the screen shows commissioning mode, then press Submit.

That's it: the heater shows up as one device with all its controls. If you already know your unit's key, choose **I already have my key** instead and paste it.

## Changing the schedule

The schedule shows up as a sensor (today's on times, with the whole week in its attributes) and is re-read every 30 minutes, so changes made on the unit appear too.

To change it, call the `secure_e7.set_schedule` action with the days you want to change. Days you leave out stay as they are.

```yaml
action: secure_e7.set_schedule
data:
  monday: [{"on": "02:15", "off": "06:00"}]
  saturday: [{"on": "02:15", "off": "06:00"}, {"on": "13:00", "off": "15:00"}]
  sunday: []
```

An empty list means off all day. The unit allows up to 3 on-times a day, in 15-minute steps. Keep the quotes around `"on"` and `"off"`, or YAML turns them into true and false. After writing, the schedule is read back from the unit, and you get an error if it doesn't match.

## If it goes unavailable

The heater is reached over Bluetooth, which is not always reliable. If it can't be reached, the integration retries every minute at first, then backs off to every 10 minutes so the proxy isn't kept busy, and recovers by itself once the heater is reachable again.

If it stays unavailable for a long time, check your Bluetooth proxy. If the proxy isn't seeing any Bluetooth devices at all, restarting it usually fixes it, and keeping its ESPHome firmware up to date helps.

## Notes

- Energy figures come from the unit's own meter and are converted at a nominal 230 V, the same way the Secure app does it.
- Only tested on one unit (an E7+ two-channel, HardwareType 65). Other versions may behave differently. If yours doesn't work, please open an issue.
- Not affiliated with or endorsed by Secure Controls or Horstmann. Use at your own risk.

## How it talks to the heater

For anyone curious, or wanting to support another model: the unit uses a Nordic UART-style Bluetooth service. Messages are JSON-RPC, AES-128 encrypted after a four-step challenge-response login, and split into numbered 19-byte chunks. The wire format is in `protocol.py`, polling and control in `coordinator.py`, the weekly schedule format in `schedule.py`, and key retrieval in `commission.py`.

## Licence

MIT. See [LICENSE](LICENSE).
