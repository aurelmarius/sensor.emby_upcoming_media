"""
Home Assistant component to feed the Upcoming Media Lovelace card with
Emby Latest Media.

https://github.com/gcorgnet/sensor.emby_upcoming_media

https://github.com/custom-cards/upcoming-media-card

"""
import logging
import json
import time
import re
import requests
import dateutil.parser
from datetime import date, datetime
from datetime import timedelta
import voluptuous as vol
from itertools import groupby
import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.components import sensor
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT, CONF_SSL
from homeassistant.helpers.entity import Entity

from .client import EmbyClient

__version__ = "0.0.1"

DOMAIN = "emby_upcoming_media"
DOMAIN_DATA = f"{DOMAIN}_data"
ATTRIBUTION = "Data is provided by Emby."

DICT_LIBRARY_TYPES = {"tvshows": "TV Shows", "movies": "Movies", "music": "Music"}

# Configuration
CONF_SENSOR = "sensor"
CONF_ENABLED = "enabled"
CONF_NAME = "name"
CONF_INCLUDE = "include"
CONF_MAX = "max"
CONF_USER_ID = "user_id"
CONF_USE_BACKDROP = "use_backdrop"
CONF_GROUP_LIBRARIES = "group_libraries"
CONF_EPISODES = "episodes"

CATEGORY_NAME = "CategoryName"
CATEGORY_ID = "CategoryId"
CATEGORY_TYPE = "CollectionType"


SCAN_INTERVAL_SECONDS = 3600  # Scan once per hour

TV_DEFAULT = {"title_default": "$title", "line1_default": "$release", "line2_default": "$number", "line3_default": "$episode", "line4_default": "Runtime: $runtime", "icon": "mdi:arrow-down-bold"}
TV_ALTERNATE = {"title_default": "$title", "line1_default": "$release • $number", "line2_default": "Average Runtime: $runtime", "line3_default": "$genres", "line4_default": "$rating", "icon": "mdi:arrow-down-bold"}
MOVIE_DEFAULT = {"title_default": "$title", "line1_default": "$release", "line2_default": "Runtime: $runtime", "line3_default": "$genres", "line4_default": "$rating", "icon": "mdi:arrow-down-bold"}
MUSIC_DEFAULT = {"title_default": "$title", "line1_default": "$studio • $release", "line2_default": "Runtime: $runtime", "line3_default": "$genres", "line4_default": "", "icon": "mdi:arrow-down-bold"}
OTHER_DEFAULT = {"title_default": "$title", "line1_default": "$release", "line2_default": "Runtime: $runtime", "line3_default": "$genres", "line4_default": "$studio", "icon": "mdi:arrow-down-bold"}

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_API_KEY): cv.string,
        vol.Optional(CONF_USER_ID): cv.string,
        vol.Optional(CONF_HOST, default="localhost"): cv.string,
        vol.Optional(CONF_PORT, default=8096): cv.port,
        vol.Optional(CONF_SSL, default=False): cv.boolean,
        vol.Optional(CONF_INCLUDE, default=[]): vol.All(cv.ensure_list),
        vol.Optional(CONF_MAX, default=5): cv.Number,
        vol.Optional(CONF_USE_BACKDROP, default=False): cv.boolean,
        vol.Optional(CONF_GROUP_LIBRARIES, default=False): cv.boolean,
        vol.Optional(CONF_EPISODES, default=True): cv.boolean
    }
)


def setup_platform(hass, config, add_devices, discovery_info=None):

    # Create DATA dict
    hass.data[DOMAIN_DATA] = {}

    # Get "global" configuration.
    api_key = config.get(CONF_API_KEY)
    host = config.get(CONF_HOST)
    ssl = config.get(CONF_SSL)
    port = config.get(CONF_PORT)
    max_items = config.get(CONF_MAX)
    user_id = config.get(CONF_USER_ID)
    include = config.get(CONF_INCLUDE)
    show_episodes = config.get(CONF_EPISODES)

    # Configure the client.
    client = EmbyClient(host, api_key, ssl, port, max_items, user_id, show_episodes)
    hass.data[DOMAIN_DATA]["client"] = client

    categories = client.get_view_categories()
    
    categories = filter(lambda el: 'CollectionType' in el.keys() and el["CollectionType"] in DICT_LIBRARY_TYPES.keys(), categories) #just include supported library types (movie/tv)

    if include != []:
        categories = filter(lambda el: el["Name"] in include, categories)
            
    if config.get(CONF_GROUP_LIBRARIES) == True:
        l=[list(y) for x,y in groupby(sorted(list(categories),key=lambda x: (x['CollectionType'])),lambda x: (x['CollectionType']))]
        categories = [{k:(v if k!='Id' else list(set([x['Id'] for x in i]))) for k,v in i[0].items()} for i in l]

    mapped = map(
        lambda cat: EmbyUpcomingMediaSensor(
            hass, {**config, CATEGORY_NAME: cat["Name"], CATEGORY_ID: cat["Id"], CATEGORY_TYPE: DICT_LIBRARY_TYPES[cat["CollectionType"]]}
        ),
        categories,
    )

    add_devices(mapped, True)


SCAN_INTERVAL = timedelta(seconds=SCAN_INTERVAL_SECONDS)


class EmbyUpcomingMediaSensor(Entity):
    def __init__(self, hass, conf):
        self._client = hass.data[DOMAIN_DATA]["client"]
        self._state = None
        self.data = []
        self.use_backdrop = conf.get(CONF_USE_BACKDROP)
        self.category_name = (conf.get(CATEGORY_TYPE) if conf.get(CONF_GROUP_LIBRARIES) == True else conf.get(CATEGORY_NAME))
        self.category_id = conf.get(CATEGORY_ID)
        self.friendly_name = "Emby Latest Media " + self.category_name
        self.entity_id = sensor.ENTITY_ID_FORMAT.format(
            "emby_latest_"
            + re.sub(
                "\_$", "", re.sub("\W+", "_", self.category_name)
            ).lower()  # remove special characters
        )

    @property
    def name(self):
        return "Latest {0} on Emby".format(self.category_name)

    @property
    def state(self):
        return self._state

    def handle_tv_episodes(self):
        """Return the state attributes."""

        attributes = {}
        default = TV_DEFAULT
        card_json = []

        card_json.append(default)

        for show in self.data:

            card_item = {}
            card_item["title"] = show["SeriesName"]
            card_item['episode'] = show.get('Name', '')

            card_item["airdate"] = show.get("PremiereDate", datetime.now().isoformat())

            if "PremiereDate" in show:
                card_item["release"] = dateutil.parser.isoparse(show.get("PremiereDate", "")).year
            else:
                card_item["release"] = ""

            if "RunTimeTicks" in show:
                timeobject = timedelta(microseconds=show["RunTimeTicks"] / 10)
                card_item["runtime"] = timeobject.total_seconds() / 60
            else:
                card_item["runtime"] = ""

            if "ParentIndexNumber" and "IndexNumber" in show:
                card_item["number"] = "S{:02d}E{:02d}".format(
                    show["ParentIndexNumber"], show["IndexNumber"]
                )
            elif "ParentIndexNumber" in show and "IndexNumber" not in show:
                card_item["number"] = "Season {:d} Special".format(
                    show["ParentIndexNumber"]
                )

            if "ParentBackdropItemId" in show:
                card_item["poster"] = self.hass.data[DOMAIN_DATA]["client"].get_image_url(
                    show["ParentBackdropItemId"], "Backdrop" if self.use_backdrop else "Primary"
                )

            card_json.append(card_item)

        attributes["data"] = json.dumps(card_json)
        attributes["attribution"] = ATTRIBUTION

        return attributes

    def handle_tv_show(self):
        """Return the state attributes."""

        attributes = {}
        default = TV_ALTERNATE
        card_json = []

        card_json.append(default)

        for show in self.data:

            card_item = {}
            card_item["title"] = show["Name"]
            card_item["airdate"] = show.get("PremiereDate", datetime.now().isoformat())

            if "PremiereDate" in show:
                card_item["release"] = dateutil.parser.isoparse(show.get("PremiereDate", "")).year

            if show["ChildCount"] > 1:
                card_item['number'] = "{0} seasons".format(
                    show["ChildCount"]
                )
            else:
                card_item['number'] = "{0} season".format(
                    show["ChildCount"]
                )

            if "RunTimeTicks" in show:
                timeobject = timedelta(microseconds=show["RunTimeTicks"] / 10)
                card_item["runtime"] = timeobject.total_seconds() / 60
            else:
                card_item["runtime"] = ""

            if "Genres" in show:
                card_item["genres"] = ", ".join(show["Genres"][:3])

            if "ParentIndexNumber" and "IndexNumber" in show:
                card_item["number"] = "S{:02d}E{:02d}".format(
                    show["ParentIndexNumber"], show["IndexNumber"]
                )

            if "CommunityRating" in show:
                card_item["rating"] = "%s %.1f" % (
                    "\u2605",  # Star character
                    show.get("CommunityRating", ""),
                )

            card_item["poster"] = self.hass.data[DOMAIN_DATA]["client"].get_image_url(
                show["Id"], "Backdrop" if self.use_backdrop else "Primary"
                )

            card_json.append(card_item)

        attributes["data"] = json.dumps(card_json)
        attributes["attribution"] = ATTRIBUTION

        return attributes

    def handle_movie(self):
        """Return the state attributes."""

        attributes = {}
        default = MOVIE_DEFAULT
        card_json = []

        card_json.append(default)

        for show in self.data:

            card_item = {}
            card_item["title"] = show["Name"]
            card_item["airdate"] = show.get("PremiereDate", datetime.now().isoformat())

            if "PremiereDate" in show:
                card_item["release"] = dateutil.parser.isoparse(show.get("PremiereDate", "")).year

            if "RunTimeTicks" in show:
                timeobject = timedelta(microseconds=show["RunTimeTicks"] / 10)
                card_item["runtime"] = timeobject.total_seconds() / 60
            else:
                card_item["runtime"] = ""

            if "Genres" in show:
                card_item["genres"] = ", ".join(show["Genres"][:3])

            if "Studios" in show and len(show["Studios"]) > 0:
                card_item["studio"] = show["Studios"][0]["Name"]

            card_item["rating"] = "%s %.1f" % (
                "\u2605",  # Star character
                show.get("CommunityRating", ""),
            )

            card_item["poster"] = self.hass.data[DOMAIN_DATA]["client"].get_image_url(
                show["Id"], "Backdrop" if self.use_backdrop else "Primary"
            )

            card_json.append(card_item)

        attributes["data"] = json.dumps(card_json)
        attributes["attribution"] = ATTRIBUTION

        return attributes

    def handle_music(self):
        """Return the state attributes."""

        attributes = {}
        default = MUSIC_DEFAULT
        card_json = []

        card_json.append(default)

        for show in self.data:

            card_item = {}
            card_item["title"] = show["Name"]
            card_item["airdate"] = show.get("PremiereDate", datetime.now().isoformat())

            if "Artists" in show and len(show["Artists"]) > 0:
                card_item["studio"] = ", ".join(show["Artists"][:3])

            if "RunTimeTicks" in show:
                timeobject = timedelta(microseconds=show["RunTimeTicks"] / 10)
                card_item["runtime"] = timeobject.total_seconds() / 60
            else:
                card_item["runtime"] = ""

            if "Genres" in show:
                card_item["genres"] = ", ".join(show["Genres"][:3])

            card_item["release"] = show.get("ProductionYear", "")
            
            if "ParentIndexNumber" in show and "IndexNumber" in show:
                card_item["number"] = "S{:02d}E{:02d}".format(
                    show["ParentIndexNumber"], show["IndexNumber"]
                )
            else:
                card_item["number"] = show.get("ProductionYear", "")

            card_item["rating"] = "%s %s" % (
                "\u2605",  # Star character
                show.get("CommunityRating", ""),
            )

            card_item["poster"] = self.hass.data[DOMAIN_DATA]["client"].get_image_url(
                show["Id"], "Primary"
            )

            card_json.append(card_item)

        attributes["data"] = json.dumps(card_json)
        attributes["attribution"] = ATTRIBUTION

        return attributes

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""

        attributes = {}
        default = OTHER_DEFAULT
        card_json = []

        if len(self.data) == 0:
            return attributes
        elif self.data[0]["Type"] == "Episode":
            return self.handle_tv_episodes()
        elif self.data[0]["Type"] == "Series":
            return self.handle_tv_show()
        elif self.data[0]["Type"] == "Movie":
            return self.handle_movie()
        elif self.data[0]["Type"] == "MusicAlbum" or "Audio":
            return self.handle_music()
        else:
            card_json.append(default)

            # for show in self.data[self._category_id]:
            for show in self.data:

                card_item = {}
                card_item["title"] = show["Name"]
                card_item["airdate"] = show.get("PremiereDate", datetime.now().isoformat())

                card_item["episode"] = show.get("OfficialRating", "")
                card_item["officialrating"] = show.get("OfficialRating", "")

                if "Genres" in show:
                    card_item["genres"] = ", ".join(show["Genres"][:3])

                if "RunTimeTicks" in show:
                    timeobject = timedelta(microseconds=show["RunTimeTicks"] / 10)
                    card_item["runtime"] = timeobject.total_seconds() / 60
                else:
                    card_item["runtime"] = ""

                if "Artists" in show and len(show["Artists"]) > 0:
                    card_item["studio"] = ", ".join(show["Artists"][:3])

                if "ParentIndexNumber" in show and "IndexNumber" in show:
                    card_item["number"] = "S{:02d}E{:02d}".format(
                        show["ParentIndexNumber"], show["IndexNumber"]
                    )
                else:
                    card_item["number"] = show.get("ProductionYear", "")

                card_item["poster"] = self.hass.data[DOMAIN_DATA]["client"].get_image_url(
                    show["Id"], "Primary"
                )

                card_item["rating"] = "%s %s" % (
                    "\u2605",  # Star character
                    show.get("CommunityRating", ""),
                )

                card_json.append(card_item)

            attributes["data"] = json.dumps(card_json)
            attributes["attribution"] = ATTRIBUTION

        return attributes

    def update(self):
        if isinstance(self.category_id, str): 
            data = self._client.get_data(self.category_id)
        else:
            data = []
            for element in self.category_id:
                for res in self._client.get_data(element):
                    data.append(res)
            data.sort(key=lambda item:item['DateCreated'], reverse=True) #as we added all libraries we now resort to get the newest at top

        if data is not None:
            self._state = "Online"
            self.data = data
        else:
            self._state = "error"
            _LOGGER.error("ERROR")
