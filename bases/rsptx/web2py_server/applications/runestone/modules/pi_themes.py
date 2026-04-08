"""
Theme bank for the analogies-based async peer instruction LLM mode.
Each theme has a display label and a hierarchy string used in the system prompt.
"""

THEMES = [
    {
        "id": "family_tree",
        "label": "Family Tree",
        "hierarchy": "family -> generation -> grandparent -> parent -> child",
    },
    {
        "id": "geographic",
        "label": "Geographic Hierarchy",
        "hierarchy": "continent -> country -> state -> city -> neighborhood",
    },
    {
        "id": "html_dom",
        "label": "HTML/DOM Tree",
        "hierarchy": "document -> html -> body -> section -> element",
    },
    {
        "id": "university",
        "label": "Organizational Hierarchy",
        "hierarchy": "university -> school -> department -> faculty -> course",
    },
    {
        "id": "apartment",
        "label": "Apartment Building",
        "hierarchy": "city -> building -> floor -> hallway -> apartment",
    },
    {
        "id": "grocery",
        "label": "Grocery Store",
        "hierarchy": "store -> department -> aisle -> shelf -> item",
    },
    {
        "id": "airport",
        "label": "Airport",
        "hierarchy": "airport -> terminal -> concourse -> gate area -> seat",
    },
    {
        "id": "discord",
        "label": "Discord Server",
        "hierarchy": "server -> category -> channel -> thread -> post",
    },
    {
        "id": "video_game",
        "label": "Video Game World",
        "hierarchy": "overworld -> region -> dungeon -> floor -> room",
    },
    {
        "id": "music_library",
        "label": "Music Library",
        "hierarchy": "genre -> artist -> album -> track -> section",
    },
    {
        "id": "audio_production",
        "label": "Audio Production",
        "hierarchy": "project -> folder -> track -> clip -> region",
    },
    {
        "id": "kitchen",
        "label": "Kitchen Storage",
        "hierarchy": "kitchen -> storage area -> section -> container -> ingredient",
    },
]

THEME_BY_ID = {t["id"]: t for t in THEMES}
