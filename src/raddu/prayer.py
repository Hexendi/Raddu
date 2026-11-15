"""Prayer times, calculated locally from the position of the sun.

No network and no dependency: the five times are computed from latitude,
longitude and the date with the standard astronomical formulas used by
every prayer-time calculator (sun declination and equation of time, then
the hour angle at which the sun reaches the depression angle of the
chosen method). Results agree with the large online calculators to
within a minute or two.

Times are returned as timezone-aware datetimes in this computer's local
time zone.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from . import config

PRAYERS = ("fajr", "dhuhr", "asr", "maghrib", "isha")
NAMES = {
    "fajr": ("Fajr", "الفجر"),
    "sunrise": ("Sunrise", "الشروق"),
    "dhuhr": ("Dhuhr", "الظهر"),
    "asr": ("Asr", "العصر"),
    "maghrib": ("Maghrib", "المغرب"),
    "isha": ("Isha", "العشاء"),
}


@dataclass(frozen=True)
class Method:
    code: str
    name: str
    fajr: float                  # sun depression angle, degrees
    isha: float                  # degrees, or minutes after maghrib if isha_minutes
    isha_minutes: bool = False


METHODS: dict[str, Method] = {m.code: m for m in (
    Method("mwl", "Muslim World League", 18.0, 17.0),
    Method("makkah", "Umm al-Qura, Makkah", 18.5, 90.0, isha_minutes=True),
    Method("egypt", "Egyptian General Authority of Survey", 19.5, 17.5),
    Method("morocco", "Ministry of Habous, Morocco", 19.0, 17.0),
    Method("karachi", "University of Islamic Sciences, Karachi", 18.0, 18.0),
    Method("isna", "Islamic Society of North America", 15.0, 15.0),
    Method("gulf", "Gulf region", 19.5, 90.0, isha_minutes=True),
)}

ASR_FACTOR = {"standard": 1, "hanafi": 2}   # shadow length; Maliki, Shafi'i, Hanbali = 1


@dataclass(frozen=True)
class Place:
    code: str
    name: str
    name_ar: str
    country: str
    lat: float
    lon: float
    method: str


# Built-in places, so a city can be picked by name without a geocoder.
# Any other place works as "lat,lon".
PLACES: dict[str, Place] = {p.code: p for p in (
    # Mauritania
    Place("nouakchott", "Nouakchott", "نواكشوط", "MR", 18.0858, -15.9785, "mwl"),
    Place("nouadhibou", "Nouadhibou", "نواذيبو", "MR", 20.9425, -17.0362, "mwl"),
    Place("rosso", "Rosso", "روصو", "MR", 16.5138, -15.8050, "mwl"),
    Place("kaedi", "Kaédi", "كيهيدي", "MR", 16.1500, -13.5000, "mwl"),
    Place("kiffa", "Kiffa", "كيفة", "MR", 16.6203, -11.4044, "mwl"),
    Place("aleg", "Aleg", "ألاك", "MR", 17.0530, -13.9132, "mwl"),
    Place("atar", "Atar", "أطار", "MR", 20.5169, -13.0499, "mwl"),
    Place("zouerat", "Zouérat", "ازويرات", "MR", 22.7354, -12.4718, "mwl"),
    Place("akjoujt", "Akjoujt", "أكجوجت", "MR", 19.7466, -14.3858, "mwl"),
    Place("tidjikja", "Tidjikja", "تجكجة", "MR", 18.5564, -11.4271, "mwl"),
    Place("ayoun", "Ayoun el Atrous", "لعيون", "MR", 16.6614, -9.6149, "mwl"),
    Place("nema", "Néma", "النعمة", "MR", 16.6170, -7.2500, "mwl"),
    Place("selibaby", "Sélibaby", "سيلبابي", "MR", 15.1590, -12.1843, "mwl"),
    Place("boutilimit", "Boutilimit", "بوتلميت", "MR", 17.5500, -14.7000, "mwl"),
    Place("chinguetti", "Chinguetti", "شنقيط", "MR", 20.4600, -12.3600, "mwl"),
    # Elsewhere
    Place("makkah", "Makkah", "مكة المكرمة", "SA", 21.4225, 39.8262, "makkah"),
    Place("madinah", "Madinah", "المدينة المنورة", "SA", 24.4672, 39.6111, "makkah"),
    Place("riyadh", "Riyadh", "الرياض", "SA", 24.7136, 46.6753, "makkah"),
    Place("cairo", "Cairo", "القاهرة", "EG", 30.0444, 31.2357, "egypt"),
    Place("dakar", "Dakar", "داكار", "SN", 14.7167, -17.4677, "mwl"),
    Place("bamako", "Bamako", "باماكو", "ML", 12.6392, -8.0029, "mwl"),
    Place("rabat", "Rabat", "الرباط", "MA", 34.0209, -6.8416, "morocco"),
    Place("casablanca", "Casablanca", "الدار البيضاء", "MA", 33.5731, -7.5898, "morocco"),
    Place("algiers", "Algiers", "الجزائر", "DZ", 36.7538, 3.0588, "mwl"),
    Place("tunis", "Tunis", "تونس", "TN", 36.8065, 10.1815, "mwl"),
    Place("tripoli", "Tripoli", "طرابلس", "LY", 32.8872, 13.1913, "egypt"),
    Place("dubai", "Dubai", "دبي", "AE", 25.2048, 55.2708, "gulf"),
    Place("doha", "Doha", "الدوحة", "QA", 25.2854, 51.5310, "gulf"),
    Place("kuwait", "Kuwait City", "الكويت", "KW", 29.3759, 47.9774, "gulf"),
    Place("amman", "Amman", "عمّان", "JO", 31.9539, 35.9106, "mwl"),
    Place("istanbul", "Istanbul", "إسطنبول", "TR", 41.0082, 28.9784, "mwl"),
    Place("karachi", "Karachi", "كراتشي", "PK", 24.8607, 67.0011, "karachi"),
    Place("jakarta", "Jakarta", "جاكرتا", "ID", -6.2088, 106.8456, "mwl"),
    Place("paris", "Paris", "باريس", "FR", 48.8566, 2.3522, "mwl"),
    Place("madrid", "Madrid", "مدريد", "ES", 40.4168, -3.7038, "mwl"),
    Place("london", "London", "لندن", "GB", 51.5074, -0.1278, "mwl"),
    Place("newyork", "New York", "نيويورك", "US", 40.7128, -74.0060, "isna"),
)}

DEFAULT_PLACE = "nouakchott"


class PrayerError(Exception):
    """Unknown place, method or Asr school."""


# -- degree trigonometry ----------------------------------------------------
def _sin(d): return math.sin(math.radians(d))
def _cos(d): return math.cos(math.radians(d))
def _tan(d): return math.tan(math.radians(d))
def _asin(x): return math.degrees(math.asin(x))
def _acos(x): return math.degrees(math.acos(max(-1.0, min(1.0, x))))
def _atan2(y, x): return math.degrees(math.atan2(y, x))
def _acot(x): return math.degrees(math.atan(1.0 / x))


def _julian(day: dt.date) -> float:
    y, m, d = day.year, day.month, day.day
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + d + b - 1524.5


def _sun(jd: float) -> tuple[float, float]:
    """(declination in degrees, equation of time in hours) for a Julian date."""
    d = jd - 2451545.0
    g = (357.529 + 0.98560028 * d) % 360
    q = (280.459 + 0.98564736 * d) % 360
    lam = (q + 1.915 * _sin(g) + 0.020 * _sin(2 * g)) % 360
    e = 23.439 - 0.00000036 * d
    ra = (_atan2(_cos(e) * _sin(lam), _cos(lam)) / 15.0) % 24
    eqt = q / 15.0 - ra
    eqt = (eqt + 12) % 24 - 12
    return _asin(_sin(e) * _sin(lam)), eqt


class _Day:
    def __init__(self, day: dt.date, lat: float, lon: float) -> None:
        self.lat = lat
        self.jd = _julian(day) - lon / (15 * 24.0)

    def noon(self, t: float) -> float:
        return (12 - _sun(self.jd + t / 24.0)[1]) % 24

    def _hour_angle(self, angle: float, t: float) -> float:
        decl = _sun(self.jd + t / 24.0)[0]
        cos_h = ((-_sin(angle) - _sin(self.lat) * _sin(decl))
                 / (_cos(self.lat) * _cos(decl)))
        return _acos(cos_h) / 15.0

    def before_noon(self, angle: float, t: float) -> float:
        return self.noon(t) - self._hour_angle(angle, t)

    def after_noon(self, angle: float, t: float) -> float:
        return self.noon(t) + self._hour_angle(angle, t)

    def asr(self, factor: int, t: float) -> float:
        decl = _sun(self.jd + t / 24.0)[0]
        angle = -_acot(factor + _tan(abs(self.lat - decl)))
        return self.after_noon(angle, t)


def times_for(day: dt.date, lat: float, lon: float, method: str = "mwl",
              asr: str = "standard") -> dict[str, dt.datetime]:
    """The six times (five prayers and sunrise) for ``day``, local time."""
    m = METHODS[method]
    factor = ASR_FACTOR[asr]
    sun = _Day(day, lat, lon)

    # hours of the day in local *solar* time; refined twice for precision
    t = {"fajr": 5.0, "sunrise": 6.0, "dhuhr": 12.0, "asr": 13.0,
         "maghrib": 18.0, "isha": 18.0}
    for _ in range(2):
        t = {
            "fajr": sun.before_noon(m.fajr, t["fajr"]),
            "sunrise": sun.before_noon(0.833, t["sunrise"]),
            "dhuhr": sun.noon(t["dhuhr"]),
            "asr": sun.asr(factor, t["asr"]),
            "maghrib": sun.after_noon(0.833, t["maghrib"]),
            "isha": (sun.after_noon(m.isha, t["isha"])
                     if not m.isha_minutes else t["isha"]),
        }
    if m.isha_minutes:
        t["isha"] = t["maghrib"] + m.isha / 60.0

    # high latitudes: when twilight never ends, cap Fajr and Isha at the
    # matching fraction of the night (the "angle-based" rule)
    night = 24 - (t["maghrib"] - t["sunrise"])
    fajr_cap = m.fajr / 60.0 * night
    if t["sunrise"] - t["fajr"] > fajr_cap or math.isnan(t["fajr"]):
        t["fajr"] = t["sunrise"] - fajr_cap
    if not m.isha_minutes:
        isha_cap = m.isha / 60.0 * night
        if t["isha"] - t["maghrib"] > isha_cap or math.isnan(t["isha"]):
            t["isha"] = t["maghrib"] + isha_cap

    midnight_utc = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
    out = {}
    for name, hours in t.items():
        utc_hours = hours - lon / 15.0
        moment = midnight_utc + dt.timedelta(hours=utc_hours)
        # round to the minute, as every timetable does
        moment = (moment + dt.timedelta(seconds=30)).replace(second=0, microsecond=0)
        out[name] = moment.astimezone()
    return out


# -- configuration ----------------------------------------------------------
@dataclass(frozen=True)
class Location:
    name: str
    lat: float
    lon: float
    method: str
    asr: str

    def times(self, day: dt.date) -> dict[str, dt.datetime]:
        return times_for(day, self.lat, self.lon, self.method, self.asr)


def parse_place(value: str) -> tuple[str, float, float, str]:
    """A place code/name or "lat,lon" -> (label, lat, lon, default method)."""
    text = value.strip()
    key = text.lower().replace(" ", "").replace("-", "").replace("é", "e")
    for p in PLACES.values():
        if key in (p.code, p.name.lower().replace(" ", "").replace("é", "e")) \
                or text == p.name_ar:
            return p.name, p.lat, p.lon, p.method
    parts = [x.strip() for x in text.split(",")]
    if len(parts) == 2:
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except ValueError:
            pass
        else:
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return f"{lat:.4f},{lon:.4f}", lat, lon, "mwl"
            raise PrayerError("latitude must be -90..90 and longitude -180..180")
    raise PrayerError(
        f"unknown place {value!r}; pick one from 'raddu --cities' "
        "or give coordinates as lat,lon (e.g. 18.0858,-15.9785)"
    )


def parse_method(value: str) -> str:
    key = value.strip().lower()
    if key not in METHODS:
        raise PrayerError(f"unknown method {value!r}; known: {', '.join(METHODS)}")
    return key


def parse_asr(value: str) -> str:
    key = value.strip().lower()
    key = {"shafii": "standard", "maliki": "standard", "hanbali": "standard"}.get(key, key)
    if key not in ASR_FACTOR:
        raise PrayerError("asr must be 'standard' (Maliki, Shafi'i, Hanbali) or 'hanafi'")
    return key


def location() -> Location:
    """The configured location, Nouakchott if none was saved."""
    data = config.load()
    try:
        name, lat, lon, method = parse_place(str(data.get("location") or DEFAULT_PLACE))
    except PrayerError:
        name, lat, lon, method = parse_place(DEFAULT_PLACE)
    try:
        method = parse_method(str(data["method"])) if data.get("method") else method
    except PrayerError:
        pass
    try:
        asr = parse_asr(str(data["asr"])) if data.get("asr") else "standard"
    except PrayerError:
        asr = "standard"
    return Location(name, lat, lon, method, asr)


def upcoming(loc: Location, after: dt.datetime) -> tuple[str, dt.datetime]:
    """The first of the five prayers strictly after ``after``."""
    day = after.astimezone().date() - dt.timedelta(days=1)
    for _ in range(4):
        times = loc.times(day)
        for name in PRAYERS:
            if times[name] > after:
                return name, times[name]
        day += dt.timedelta(days=1)
    raise RuntimeError("no prayer time found")   # unreachable outside the poles
