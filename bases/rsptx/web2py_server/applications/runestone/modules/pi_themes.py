"""
Theme bank for the analogies-based async peer instruction LLM mode.
Each theme has a display label and a hierarchy string used in the system prompt.
"""

THEMES = [
    {"id": "family_tree", "label": "Family Tree"},
    {"id": "geographic", "label": "Geographic Hierarchy"},
    {"id": "html_dom", "label": "HTML/DOM Tree"},
    {"id": "university", "label": "Organizational Hierarchy"},
    {"id": "apartment", "label": "Apartment Building"},
    {"id": "grocery", "label": "Grocery Store"},
    {"id": "airport", "label": "Airport"},
    {"id": "discord", "label": "Discord Server"},
    {"id": "video_game", "label": "Video Game World"},
    {"id": "music_library", "label": "Music Library"},
    {"id": "audio_production", "label": "Audio Production"},
    {"id": "kitchen", "label": "Kitchen Storage"},
]

THEME_BY_ID = {t["id"]: t for t in THEMES}
