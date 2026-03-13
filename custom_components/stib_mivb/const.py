"""Constants for the STIB/MIVB integration."""

DOMAIN = "stib_mivb"
DEFAULT_SCAN_INTERVAL = 30  # seconds

CONF_LANGUAGE = "language"
CONF_API_KEY = "api_key"
CONF_STOP_SEARCH = "stop_search"
CONF_STOP_NAME = "stop_name"
CONF_STOP_GROUPS = "stop_groups"  # list of grouped stop entries
CONF_SCAN_INTERVAL = "scan_interval"

LANGUAGE_FRENCH = "fr"
LANGUAGE_DUTCH = "nl"

# Authenticated endpoint (requires bmc-partner-key header)
API_BASE = "https://api-management-opendata-production.azure-api.net/api/datasets/stibmivb"
API_STOPS_BY_LINE = f"{API_BASE}/static/stopsByLine"
API_STOP_DETAILS = f"{API_BASE}/static/StopDetails"
API_WAITING_TIMES = f"{API_BASE}/rt/WaitingTimes"

API_KEY_HEADER = "bmc-partner-key"

ATTR_NEXT_PASSAGE = "next_passage"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_STOP_NAME_FR = "stop_name_fr"
ATTR_STOP_NAME_NL = "stop_name_nl"
ATTR_DIRECTION = "direction"
ATTR_DESTINATION = "destination"
ATTR_LINE_ID = "line_id"
ATTR_POINT_IDS = "point_ids"
