# Trader id -> normalizedName, from https://json.tarkov.dev/pve/traders
TRADER_NAMES = {
    "54cb50c76803fa8b248b4571": "prapor",
    "54cb57776803fa99248b456e": "therapist",
    "579dc571d53a0658a154fbec": "fence",
    "58330581ace78e27b8b10cee": "skier",
    "5935c25fb3acc3127c3d8cd9": "peacekeeper",
    "5a7c2eca46aef81a7ca2145d": "mechanic",
    "5ac3b934156ae10c4430e83c": "ragman",
    "5c0647fdd443bc2504c2d371": "jaeger",
    "638f541a29ffd1183d187f57": "lightkeeper",
}


def trader_display_name(trader_id):
    normalized = TRADER_NAMES.get(trader_id)
    return normalized.title() if normalized else trader_id
