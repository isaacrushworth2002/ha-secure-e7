# Secure / Horstmann E7+ immersion heater for Home Assistant

Control the Secure (Horstmann) E7+ immersion heater controller (SCE100) from Home Assistant over Bluetooth, instead of the Secure phone app.

Handy if you're on Economy 7 or a flexible tariff and want to heat water in the cheap slots, boost it from an automation, or see how much energy it's using.

**Before you start:** setting it up claims the controller for Home Assistant, and the Secure phone app stops working with it.

## What you get

- Hot water on/off, and boost with a choice of 30, 60 or 120 minutes
- A Timed Boost switch for the unit's boost programme (it does not stop the off-peak run)
- The weekly schedule itself, which you can read and change from Home Assistant
- Current mode, boost time left, and when the next scheduled change is
- Daily energy use and hours from the unit's own meter, split into boost and off-peak, with the scheduled hours alongside the actual ones. Energy is imported as long-term statistics so each day is booked to its own date

## What you need

- Home Assistant 2025.11 or newer
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

## What each control does

The unit keeps two programmes. The off-peak programme is the daily window that heats the cylinder (for example 02:15 to 06:00). The boost programme is a second, separate timer, empty by default.

| Control | What it does |
| --- | --- |
| Hot Water switch | Starts a boost of the chosen length, or cancels it |
| Boost Duration | How long the Hot Water switch boosts for (30, 60 or 120 minutes) |
| Power switch | Home or off for the whole unit |
| Timed Boost switch | Turns the boost programme on or off. It does not stop the off-peak run, so switching it off will not stop the heater at 02:15 |
| Schedule sensor and `set_schedule` | Read and write the off-peak programme |
| Next Change Time and State | The next on or off edge of the off-peak programme, from the schedule read, in the unit's local time |
| Heating | Whether the element is switched on, by a boost or by the off-peak channel. The `basis` attribute says how it is known |
| Panel Lock | Diagnostic: whether the front panel is locked |
| Daylight Saving | Diagnostic: whether the heater clock follows daylight saving |
| Estimated Power | Your element's power while it is switched on, or 0. The unit reports no live power, voltage or current |
| Timer State | Diagnostic: the off-peak channel's raw on/off value, with every raw value from the last poll |
| Energy and Hours sensors | The newest metered day. Their `date` attribute says which day |
| Scheduled Hours sensors | Diagnostic: hours the unit scheduled that day, for comparison with the actual hours |

## Estimated power and heating

Nothing about your element is assumed. Its power is learned from the unit's own meter, as a day's energy divided by the time the element ran, taking the middle value of the last 14 days that ran for at least 24 minutes, separately for off-peak and boost. Until one of the two has such a day it uses the other's figure, and until either does, Estimated Power reads unknown while the element is on. The `rating_source` attribute says where the figure comes from. To set it yourself, give `rating_watts` in a `secure_e7:` YAML block when the heater is first added. A stored value of 3000 from an earlier version was the old default, so it is dropped.

Heating and Estimated Power say what the unit has switched on, not what the element is drawing. The unit does not report its thermostat, which usually cuts out well before the end of an off-peak window and then tops up now and again, so during a window both can read on while nothing is being used. The `basis` attribute is:

- `hot_water`: a boost, or the boost programme
- `timer_state`: the unit's own on/off value for the off-peak channel
- `programme`: the unit sent no timer state, the time is inside today's off-peak window and the power is on
- `power_off`, `idle` or `unknown`

If the unit's Daylight Saving setting is off, its programme is taken to run on standard time. For accurate energy use the "Secure E7 Energy" statistic, which comes from the meter. Adding up Estimated Power over time reads high.

## Changing the schedule

This is the off-peak programme. The schedule shows up as a sensor (today's on times, with the whole week in its attributes) and is re-read every 30 minutes, so changes made on the unit appear too.

To change it, call the `secure_e7.set_schedule` action with the days you want to change. Days you leave out stay as they are.

```yaml
action: secure_e7.set_schedule
data:
  monday: [{"on": "02:15", "off": "06:00"}]
  saturday: [{"on": "02:15", "off": "06:00"}, {"on": "13:00", "off": "15:00"}]
  sunday: []
```

An empty list means off all day. The unit allows up to 3 on-times a day, in 15-minute steps, and the latest off time you can set is 23:45. Keep the quotes around `"on"` and `"off"`, or YAML turns them into true and false. After writing, the schedule is read back from the unit, and you get an error if it doesn't match.

## If it goes unavailable

The heater is reached over Bluetooth, which is not always reliable. If it can't be reached, the integration retries every minute at first, then backs off to every 5 minutes so the proxy isn't kept busy, and recovers by itself once the heater is reachable again. If the heater is heard advertising while polls are failing, it tries again straight away, at most once every 5 minutes. While it has never been reached since a restart, its entities show as unavailable.

If it stays unavailable for a long time, check your Bluetooth proxy. If the proxy isn't seeing any Bluetooth devices at all, restarting it usually fixes it, and keeping its ESPHome firmware up to date helps.

## Notes

- To use the energy in the Energy dashboard, add the statistic "Secure E7 Energy" (not the Energy Today sensor) as an individual device. The unit only meters whole days, so the hours are worked out. Off-peak kWh is placed from the start of that weekday's window for as many minutes as the element actually ran, which matches the real burn when the thermostat keeps the element on until the cylinder is hot. When it cycles, some of the energy lands in the wrong hour. Boost kWh is placed where the integration saw a boost running in its polls; a boost it did not see, and any day whose weekday has no window in the current programme, shows in the first hour of that day. Each day's windows are remembered when the day is first seen, so editing the programme later does not move earlier days; days from before that was kept use the programme as it is now.
- The Energy Today sensors have no state class, because the unit fills its rows in late and the Energy dashboard would book them to the wrong hour. If an earlier version gave them one, Home Assistant may show a repair under Developer tools, Statistics saying the entity no longer has a state class. It is safe to choose Delete for those.
- Energy figures come from the unit's own meter, which counts amp-hours. They are converted at a fixed 230 V, the same way the Secure app does it. A real supply varies a little either side of that, so treat the kWh as close rather than exact.
- If you used the old Follow Schedule switch, it is now the Timed Boost switch. It keeps its entity id (by default `switch.immersion_heater_follow_schedule`), so automations carry on working, but they now control the boost programme only.
- Only tested on one unit (an E7+ two-channel, HardwareType 65). Other versions may behave differently. If yours doesn't work, please open an issue.
- Not affiliated with or endorsed by Secure Controls or Horstmann. Use at your own risk.

## Licence

MIT. See [LICENSE](LICENSE).
