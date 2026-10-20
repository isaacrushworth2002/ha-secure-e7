"""Constants for the Secure / Horstmann E7+ immersion integration."""
from __future__ import annotations

DOMAIN = "secure_e7"

# Nordic UART service on the E7+
SVC_UUID = "b973f2e0-b19e-11e2-9e96-0800200c9a66"
RX_UUID = "da73f3e1-b19e-11e2-9e96-0800200c9a66"  # write
TX_UUID = "e973f2e2-b19e-11e2-9e96-0800200c9a66"  # notify

# JSON-RPC service ids (ServiceHeaderDTO.ServiceType)
SI_BBSERVER = 1
SI_THERMOSTAT = 15
SI_HOTWATER = 16
SI_TIMER = 33

# Handler ids
HI_WRITE = 2
HI_GET_ALL = 3  # GetAllServiceValues (on BBServer)

# DeviceCharacteristics ("I" inside a CharacteristicDTO)
CH_HOTWATER_STATE = 4
CH_MODE = 6
CH_NEXT_PERIOD = 9       # ScheduleNextPeriod: clock time of next change, minutes past midnight
CH_NEXT_SETPOINT = 10    # ScheduleNextSetPoint: 1 = next period On, 0 = Off
CH_SCHEDULE_ENABLE = 27

# Sentinel meaning "no next scheduled period" (Constants.MAX_TIME, 0xFFFF)
MAX_TIME = 65535

# --- Consumption (real metered energy) -----------------------------------
# The device DOES report energy, via a separate path the app uses for its
# "Consumption Trends" screen: the DynamicTariff service, handler
# getConsumptionState, arg = channel businessObjectId. It returns, per day,
# accumulated charge in centi-amp-hours split into boost vs off-peak, which the
# app turns into kWh with a fixed nominal 230 V (no voltage is read on this
# path). So energy here is metered amp-hours x nominal 230 V, not a guess.
SI_DYNAMIC_TARIFF = 36   # ServiceHeaderDTO.ServiceType.DynamicTariff
HI_GET_CONSUMPTION = 9   # HandlerIdDynamicTariffManagement.getConsumptionState
NOMINAL_VOLTAGE = 230.0
CENTI_AH_TO_KWH = NOMINAL_VOLTAGE / 100000.0   # multiply raw BP/OP by this

# DynamicDaySchedule @SerializedName field keys (one row == one UTC day)
K_TIME = "T"              # unix seconds; 0 = empty bucket
K_OFFPEAK_CHARGE = "OP"   # centi-amp-hours off-peak
K_BOOST_CHARGE = "BP"     # centi-amp-hours during boost
K_OFFPEAK_SCHED_MIN = "OS"
K_BOOST_SCHED_MIN = "BS"
K_OFFPEAK_ACTUAL_MIN = "OA"
K_BOOST_ACTUAL_MIN = "BA"
K_DTO_DAYS = "D"
K_DTO_ID = "I"

# --- Weekly schedule (see schedule.py) -------------------------------------
# Read and write verified against a real unit and its console on 2026-10-18.
# set_schedule still reads back after writing and fails loudly on mismatch.
SI_SCHEDULE = 17         # ServiceType for the weekly programme
SCHEDULE_OBJECT_ID = 1   # object 1 holds the console programme (object 2 is all zeros)
HI_GET_SCHEDULE = 22     # read handler (verified)
HI_SET_SCHEDULE = 21     # write handler (verified)
SERVICE_GET_SCHEDULE = "get_schedule"
SERVICE_SET_SCHEDULE = "set_schedule"

# Fetch consumption far less often than live state (rows are whole days)
CONSUMPTION_EVERY_N_POLLS = 30   # ~30 min at a 60 s poll

# The estimated power/energy below is a live-today fallback for the gap between
# daily consumption rows; real per-day kWh comes from the DynamicTariff path.

# OverrideType ("OT")
OT_NONE = 0
OT_ADVANCE = 2  # boost

# Mode values (HomeAway) used as "V" of a Mode characteristic
MODE_OFF = 0
MODE_AWAY = 1
MODE_HOME = 2

MODE_NAMES = {MODE_OFF: "off", MODE_AWAY: "away", MODE_HOME: "home"}

# Config keys
CONF_MAC = "mac"
CONF_KEY = "key"
CONF_GMI = "gmi"
CONF_BOOST_MINUTES = "boost_minutes"
CONF_RATING_WATTS = "rating_watts"

DEFAULT_BOOST_MINUTES = 60
ALLOWED_BOOST_MINUTES = (30, 60, 120)
DEFAULT_RATING_WATTS = 3000  # typical UK immersion element; change to match yours

SCAN_INTERVAL_SECONDS = 60

# BLE reliability. An ESPHome proxy stops scanning while it tries to connect,
# so keep connection retries short. Every poll or command is time-limited so a
# hung connection cannot hold the lock for ever; the limit allows for three
# connection attempts plus the slower polls that also read consumption and
# the schedule.
CONNECT_MAX_ATTEMPTS = 3
OPERATION_TIMEOUT_SECONDS = 120
DISCONNECT_TIMEOUT_SECONDS = 10
REPLY_TIMEOUT_SECONDS = 12  # wait for the last chunk of one reply

PLATFORMS = ["binary_sensor", "sensor", "switch", "select"]
