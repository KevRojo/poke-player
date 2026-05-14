"""Pokemon FireRed (USA) memory reader — Phase 2 implementation.

FireRed runs on the GBA and uses a very different memory layout from the
original Red/Blue.  Notably, party and box Pokemon data is **encrypted**:

  * Each Pokemon has a 100-byte data structure (48 bytes encrypted).
  * The 48-byte encrypted block is split into four 12-byte substructures
    (Growth, Attacks, EVs/Condition, Misc).
  * The substructure order is determined by ``personality_value % 24``.
  * Encryption key = ``personality_value XOR original_trainer_id``.
  * Each 4-byte word of the 48-byte block is XOR'd with the key.

Key EWRAM addresses (FireRed USA 1.0, SQUIRRELS offsets):

  * Save Block 1 pointer : 0x0300500C
  * Save Block 2 pointer : 0x03005010
  * Party data           : SaveBlock1 + 0x0234
  * Bag                  : SaveBlock1 + 0x0310
  * Money                : SaveBlock1 + 0x0290 (XOR-encrypted with security key)
  * Player name          : SaveBlock2 + 0x0000 (8 bytes, Gen 3 encoding)
  * Badges low           : SaveBlock2 + 0x00F8
  * Map group/number     : SaveBlock1 + 0x0004
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional, Tuple

from poke_player.emulator import Emulator
from poke_player.memory.reader import GameMemoryReader


# ===================================================================
# Address constants (FireRed USA 1.0)
# ===================================================================

ADDR_SAVEBLOCK1_PTR = 0x0300500C
ADDR_SAVEBLOCK2_PTR = 0x03005010

# Offsets from SaveBlock1
OFF_PARTY_COUNT = 0x0234
OFF_PARTY_DATA = 0x0238  # 100 bytes × 6
OFF_MONEY = 0x0290  # 4 bytes, XOR-encrypted with security key
OFF_SECURITY_KEY = 0x00F20  # From SaveBlock2 + 0x00F20 (for decryption)
OFF_BAG_ITEMS = 0x0310
OFF_MAP_GROUP = 0x0004
OFF_MAP_NUMBER = 0x0005
OFF_POS_X = 0x0000  # local coords within map
OFF_POS_Y = 0x0002

# Offsets from SaveBlock2
OFF_PLAYER_NAME = 0x0000  # 8 bytes
OFF_PLAYER_GENDER = 0x0008
OFF_TRAINER_ID = 0x000A  # 4 bytes (TID + SID)
OFF_PLAY_TIME = 0x000E  # hours(2) + minutes(1) + seconds(1)
OFF_BADGES = 0x00F8  # 2 bytes bitmask

# Pokemon substructure order lookup (24 permutations)
SUBSTRUCTURE_ORDER = [
    "GAEM", "GAME", "GEAM", "GEMA", "GMAE", "GMEA",
    "AGEM", "AGME", "AEGM", "AEMG", "AMGE", "AMEG",
    "EGAM", "EGMA", "EAGM", "EAMG", "EMGA", "EMAG",
    "MGAE", "MGEA", "MAGE", "MAEG", "MEGA", "MEAG",
]

PARTY_MON_SIZE_GEN3 = 100
ENCRYPTED_BLOCK_SIZE = 48
SUBSTRUCTURE_SIZE = 12

# Gen 3 character encoding table (FireRed/LeafGreen)
# Based on actual Gen 3 encoding used in GBA Pokemon games
GEN3_ENCODING = {
    0x00: " ", 0x01: "À", 0x02: "Á", 0x03: "Â", 0x04: "Ç", 0x05: "È",
    0x06: "É", 0x07: "Ê", 0x08: "Ë", 0x09: "Ì", 0x0B: "Î", 0x0C: "Ï",
    0x0D: "Ò", 0x0E: "Ó", 0x0F: "Ô", 0x10: "Œ", 0x11: "Ù", 0x12: "Ú",
    0x13: "Û", 0x14: "Ñ", 0x15: "ß", 0x16: "à", 0x17: "á", 0x19: "ç",
    0x1A: "è", 0x1B: "é", 0x1C: "ê", 0x1D: "ë", 0x1E: "ì", 0x20: "î",
    0x21: "ï", 0x22: "ò", 0x23: "ó", 0x24: "ô", 0x25: "œ", 0x26: "ù",
    0x27: "ú", 0x28: "û", 0x29: "ñ", 0x2A: "º", 0x2B: "ª", 0x2C: "&",
    0x2D: "+", 0x2E: "", 0x34: "[Lv]", 0x35: "=", 0x36: ";", 0x51: "¿",
    0x52: "¡", 0x53: "[Pk]", 0x54: "[Mn]", 0x55: "[Po]", 0x56: "[Ke]",
    0x57: "[Bl]", 0x58: "[Oc]", 0x59: "[K]", 0x5A: "Í", 0x5B: "%", 0x5C: "(",
    0x5D: ")", 0x68: "â", 0x6F: "í", 0x79: "↑", 0x7A: "↓", 0x7B: "←",
    0x7C: "→", 0x7D: "*", 0x7E: "*", 0x7F: "*", 0x80: "*", 0x81: "*",
    0x82: "*", 0x83: "*", 0x84: "*",
    # Uppercase A-Z: 0xBB to 0xD4
    0xBB: "A", 0xBC: "B", 0xBD: "C", 0xBE: "D", 0xBF: "E",
    0xC0: "F", 0xC1: "G", 0xC2: "H", 0xC3: "I", 0xC4: "J",
    0xC5: "K", 0xC6: "L", 0xC7: "M", 0xC8: "N", 0xC9: "O",
    0xCA: "P", 0xCB: "Q", 0xCC: "R", 0xCD: "S", 0xCE: "T",
    0xCF: "U", 0xD0: "V", 0xD1: "W", 0xD2: "X", 0xD3: "Y", 0xD4: "Z",
    # Lowercase a-z: 0xD5 to 0xEE
    0xD5: "a", 0xD6: "b", 0xD7: "c", 0xD8: "d", 0xD9: "e",
    0xDA: "f", 0xDB: "g", 0xDC: "h", 0xDD: "i", 0xDE: "j",
    0xDF: "k", 0xE0: "l", 0xE1: "m", 0xE2: "n", 0xE3: "o",
    0xE4: "p", 0xE5: "q", 0xE6: "r", 0xE7: "s", 0xE8: "t",
    0xE9: "u", 0xEA: "v", 0xEB: "w", 0xEC: "x", 0xED: "y", 0xEE: "z",
    # Special chars
    0xEF: "à", 0xF0: "è", 0xF1: "é", 0xF2: "ù", 0xF3: "À",
    0xF4: "È", 0xF5: "É", 0xF6: "Ù", 0xF7: "Ç", 0xF8: "º",
    0xF9: "ª", 0xFA: "ç", 0xFB: "[ ]", 0xFC: "[ ]", 0xFD: "[ ]",
    0xFE: "[ ]", 0xFF: "",
}

# Pokemon species names (Gen 3 - first 151 for now)
SPECIES_NAMES = {
    0: "????????", 1: "Bulbasaur", 2: "Ivysaur", 3: "Venusaur",
    4: "Charmander", 5: "Charmeleon", 6: "Charizard",
    7: "Squirtle", 8: "Wartortle", 9: "Blastoise",
    10: "Caterpie", 11: "Metapod", 12: "Butterfree",
    13: "Weedle", 14: "Kakuna", 15: "Beedrill",
    16: "Pidgey", 17: "Pidgeotto", 18: "Pidgeot",
    19: "Rattata", 20: "Raticate", 21: "Spearow", 22: "Fearow",
    23: "Ekans", 24: "Arbok", 25: "Pikachu", 26: "Raichu",
    27: "Sandshrew", 28: "Sandslash", 29: "Nidoran♀", 30: "Nidorina",
    31: "Nidoqueen", 32: "Nidoran♂", 33: "Nidorino", 34: "Nidoking",
    35: "Clefairy", 36: "Clefable", 37: "Vulpix", 38: "Ninetales",
    39: "Jigglypuff", 40: "Wigglytuff", 41: "Zubat", 42: "Golbat",
    43: "Oddish", 44: "Gloom", 45: "Vileplume", 46: "Paras", 47: "Parasect",
    48: "Venonat", 49: "Venomoth", 50: "Diglett", 51: "Dugtrio",
    52: "Meowth", 53: "Persian", 54: "Psyduck", 55: "Golduck",
    56: "Mankey", 57: "Primeape", 58: "Growlithe", 59: "Arcanine",
    60: "Poliwag", 61: "Poliwhirl", 62: "Poliwrath", 63: "Abra",
    64: "Kadabra", 65: "Alakazam", 66: "Machop", 67: "Machoke",
    68: "Machamp", 69: "Bellsprout", 70: "Weepinbell", 71: "Victreebel",
    72: "Tentacool", 73: "Tentacruel", 74: "Geodude", 75: "Graveler",
    76: "Golem", 77: "Ponyta", 78: "Rapidash", 79: "Slowpoke",
    80: "Slowbro", 81: "Magnemite", 82: "Magneton", 83: "Farfetch'd",
    84: "Doduo", 85: "Dodrio", 86: "Seel", 87: "Dewgong",
    88: "Grimer", 89: "Muk", 90: "Shellder", 91: "Cloyster",
    92: "Gastly", 93: "Haunter", 94: "Gengar", 95: "Onix",
    96: "Drowzee", 97: "Hypno", 98: "Krabby", 99: "Kingler",
    100: "Voltorb", 101: "Electrode", 102: "Exeggcute", 103: "Exeggutor",
    104: "Cubone", 105: "Marowak", 106: "Hitmonlee", 107: "Hitmonchan",
    108: "Lickitung", 109: "Koffing", 110: "Weezing", 111: "Rhyhorn",
    112: "Rhydon", 113: "Chansey", 114: "Tangela", 115: "Kangaskhan",
    116: "Horsea", 117: "Seadra", 118: "Goldeen", 119: "Seaking",
    120: "Staryu", 121: "Starmie", 122: "Mr. Mime", 123: "Scyther",
    124: "Jynx", 125: "Electabuzz", 126: "Magmar", 127: "Pinsir",
    128: "Tauros", 129: "Magikarp", 130: "Gyarados", 131: "Lapras",
    132: "Ditto", 133: "Eevee", 134: "Vaporeon", 135: "Jolteon",
    136: "Flareon", 137: "Porygon", 138: "Omanyte", 139: "Omastar",
    140: "Kabuto", 141: "Kabutops", 142: "Aerodactyl", 143: "Snorlax",
    144: "Articuno", 145: "Zapdos", 146: "Moltres", 147: "Dratini",
    148: "Dragonair", 149: "Dragonite", 150: "Mewtwo", 151: "Mew",
    # Gen 2
    152: "Chikorita", 153: "Bayleef", 154: "Meganium", 155: "Cyndaquil",
    156: "Quilava", 157: "Typhlosion", 158: "Totodile", 159: "Croconaw",
    160: "Feraligatr", 161: "Sentret", 162: "Furret", 163: "Hoothoot",
    164: "Noctowl", 165: "Ledyba", 166: "Ledian", 167: "Spinarak",
    168: "Ariados", 169: "Crobat", 170: "Chinchou", 171: "Lanturn",
    172: "Pichu", 173: "Cleffa", 174: "Igglybuff", 175: "Togepi",
    176: "Togetic", 177: "Natu", 178: "Xatu", 179: "Mareep",
    180: "Flaaffy", 181: "Ampharos", 182: "Bellossom", 183: "Marill",
    184: "Azumarill", 185: "Sudowoodo", 186: "Politoed", 187: "Hoppip",
    188: "Skiploom", 189: "Jumpluff", 190: "Aipom", 191: "Sunkern",
    192: "Sunflora", 193: "Yanma", 194: "Wooper", 195: "Quagsire",
    196: "Espeon", 197: "Umbreon", 198: "Murkrow", 199: "Slowking",
    200: "Misdreavus", 201: "Unown", 202: "Wobbuffet", 203: "Girafarig",
    204: "Pineco", 205: "Forretress", 206: "Dunsparce", 207: "Gligar",
    208: "Steelix", 209: "Snubbull", 210: "Granbull", 211: "Qwilfish",
    212: "Scizor", 213: "Shuckle", 214: "Heracross", 215: "Sneasel",
    216: "Teddiursa", 217: "Ursaring", 218: "Slugma", 219: "Magcargo",
    220: "Swinub", 221: "Piloswine", 222: "Corsola", 223: "Remoraid",
    224: "Octillery", 225: "Delibird", 226: "Mantine", 227: "Skarmory",
    228: "Houndour", 229: "Houndoom", 230: "Kingdra", 231: "Phanpy",
    232: "Donphan", 233: "Porygon2", 234: "Stantler", 235: "Smeargle",
    236: "Tyrogue", 237: "Hitmontop", 238: "Smoochum", 239: "Elekid",
    240: "Magby", 241: "Miltank", 242: "Blissey", 243: "Raikou",
    244: "Entei", 245: "Suicune", 246: "Larvitar", 247: "Pupitar",
    248: "Tyranitar", 249: "Lugia", 250: "Ho-Oh", 251: "Celebi",
    # Gen 3
    252: "Treecko", 253: "Grovyle", 254: "Sceptile", 255: "Torchic",
    256: "Combusken", 257: "Blaziken", 258: "Mudkip", 259: "Marshtomp",
    260: "Swampert", 261: "Poochyena", 262: "Mightyena", 263: "Zigzagoon",
    264: "Linoone", 265: "Wurmple", 266: "Silcoon", 267: "Beautifly",
    268: "Cascoon", 269: "Dustox", 270: "Lotad", 271: "Lombre",
    272: "Ludicolo", 273: "Seedot", 274: "Nuzleaf", 275: "Shiftry",
    276: "Taillow", 277: "Swellow", 278: "Wingull", 279: "Pelipper",
    280: "Ralts", 281: "Kirlia", 282: "Gardevoir", 283: "Surskit",
    284: "Masquerain", 285: "Shroomish", 286: "Breloom", 287: "Slakoth",
    288: "Vigoroth", 289: "Slaking", 290: "Nincada", 291: "Ninjask",
    292: "Shedinja", 293: "Whismur", 294: "Loudred", 295: "Exploud",
    296: "Makuhita", 297: "Hariyama", 298: "Azurill", 299: "Nosepass",
    300: "Skitty", 301: "Delcatty", 302: "Sableye", 303: "Mawile",
    304: "Aron", 305: "Lairon", 306: "Aggron", 307: "Meditite",
    308: "Medicham", 309: "Electrike", 310: "Manectric", 311: "Plusle",
    312: "Minun", 313: "Volbeat", 314: "Illumise", 315: "Roselia",
    316: "Gulpin", 317: "Swalot", 318: "Carvanha", 319: "Sharpedo",
    320: "Wailmer", 321: "Wailord", 322: "Numel", 323: "Camerupt",
    324: "Torkoal", 325: "Spoink", 326: "Grumpig", 327: "Spinda",
    328: "Trapinch", 329: "Vibrava", 330: "Flygon", 331: "Cacnea",
    332: "Cacturne", 333: "Swablu", 334: "Altaria", 335: "Zangoose",
    336: "Seviper", 337: "Lunatone", 338: "Solrock", 339: "Barboach",
    340: "Whiscash", 341: "Corphish", 342: "Crawdaunt", 343: "Baltoy",
    344: "Claydol", 345: "Lileep", 346: "Cradily", 347: "Anorith",
    348: "Armaldo", 349: "Feebas", 350: "Milotic", 351: "Castform",
    352: "Kecleon", 353: "Shuppet", 354: "Banette", 355: "Duskull",
    356: "Dusclops", 357: "Tropius", 358: "Chimecho", 359: "Absol",
    360: "Wynaut", 361: "Snorunt", 362: "Glalie", 363: "Spheal",
    364: "Sealeo", 365: "Walrein", 366: "Clamperl", 367: "Huntail",
    368: "Gorebyss", 369: "Relicanth", 370: "Luvdisc", 371: "Bagon",
    372: "Shelgon", 373: "Salamence", 374: "Beldum", 375: "Metang",
    376: "Metagross", 377: "Regirock", 378: "Regice", 379: "Registeel",
    380: "Latias", 381: "Latios", 382: "Kyogre", 383: "Groudon",
    384: "Rayquaza", 385: "Jirachi", 386: "Deoxys",
}

# Item names (Gen 3 - subset of most common items)
ITEM_NAMES = {
    0: "????????", 1: "Master Ball", 2: "Ultra Ball", 3: "Great Ball",
    4: "Poké Ball", 5: "Safari Ball", 6: "Net Ball", 7: "Dive Ball",
    8: "Nest Ball", 9: "Repeat Ball", 10: "Timer Ball", 11: "Luxury Ball",
    12: "Premier Ball", 13: "Potion", 14: "Antidote", 15: "Burn Heal",
    16: "Ice Heal", 17: "Awakening", 18: "Paralyze Heal", 19: "Full Restore",
    20: "Max Potion", 21: "Hyper Potion", 22: "Super Potion", 23: "Full Heal",
    24: "Revive", 25: "Max Revive", 26: "Fresh Water", 27: "Soda Pop",
    28: "Lemonade", 29: "Moomoo Milk", 30: "Energy Powder", 31: "Energy Root",
    32: "Heal Powder", 33: "Revival Herb", 34: "Ether", 35: "Max Ether",
    36: "Elixir", 37: "Max Elixir", 38: "Lava Cookie", 39: "Blue Flute",
    40: "Yellow Flute", 41: "Red Flute", 42: "Black Flute", 43: "White Flute",
    44: "Berry Juice", 45: "Sacred Ash", 46: "Shoal Salt", 47: "Shoal Shell",
    48: "Red Shard", 49: "Blue Shard", 50: "Yellow Shard", 51: "Green Shard",
    63: "HP Up", 64: "Protein", 65: "Iron", 66: "Carbos", 67: "Calcium",
    68: "Rare Candy", 69: "PP Up", 70: "Zinc", 71: "PP Max",
    73: "Guard Spec.", 74: "Dire Hit", 75: "X Attack", 76: "X Defend",
    77: "X Speed", 78: "X Accuracy", 79: "X Special", 80: "Poké Doll",
    81: "Fluffy Tail", 83: "Super Repel", 84: "Max Repel", 85: "Escape Rope",
    86: "Repel", 93: "Sun Stone", 94: "Moon Stone", 95: "Fire Stone",
    96: "Thunder Stone", 97: "Water Stone", 98: "Leaf Stone",
    103: "Tiny Mushroom", 104: "Big Mushroom", 106: "Pearl", 107: "Big Pearl",
    108: "Stardust", 109: "Star Piece", 110: "Nugget", 111: "Heart Scale",
    121: "Orange Mail", 122: "Harbor Mail", 123: "Glitter Mail",
    124: "Mech Mail", 125: "Wood Mail", 126: "Wave Mail", 127: "Bead Mail",
    128: "Shadow Mail", 129: "Tropic Mail", 130: "Dream Mail",
    131: "Fab Mail", 132: "Retro Mail", 133: "Cheri Berry",
    134: "Chesto Berry", 135: "Pecha Berry", 136: "Rawst Berry",
    137: "Aspear Berry", 138: "Leppa Berry", 139: "Oran Berry",
    140: "Persim Berry", 141: "Lum Berry", 142: "Sitrus Berry",
    143: "Figy Berry", 144: "Wiki Berry", 145: "Mago Berry",
    146: "Aguav Berry", 147: "Iapapa Berry", 148: "Razz Berry",
    149: "Bluk Berry", 150: "Nanab Berry", 151: "Wepear Berry",
    152: "Pinap Berry", 153: "Pomeg Berry", 154: "Kelpsy Berry",
    155: "Qualot Berry", 156: "Hondew Berry", 157: "Grepa Berry",
    158: "Tamato Berry", 159: "Cornn Berry", 160: "Magost Berry",
    161: "Rabuta Berry", 162: "Nomel Berry", 163: "Spelon Berry",
    164: "Pamtre Berry", 165: "Watmel Berry", 166: "Durin Berry",
    167: "Belue Berry", 168: "Liechi Berry", 169: "Ganlon Berry",
    170: "Salac Berry", 171: "Petaya Berry", 172: "Apicot Berry",
    173: "Lansat Berry", 174: "Starf Berry", 175: "Enigma Berry",
    176: "MysticTicket", 177: "AuroraTicket", 178: "Powder Jar",
    179: "Ruby", 180: "Sapphire",
    219: "TM01", 220: "TM02", 221: "TM03", 222: "TM04", 223: "TM05",
    224: "TM06", 225: "TM07", 226: "TM08", 227: "TM09", 228: "TM10",
    229: "TM11", 230: "TM12", 231: "TM13", 232: "TM14", 233: "TM15",
    234: "TM16", 235: "TM17", 236: "TM18", 237: "TM19", 238: "TM20",
    239: "TM21", 240: "TM22", 241: "TM23", 242: "TM24", 243: "TM25",
    244: "TM26", 245: "TM27", 246: "TM28", 247: "TM29", 248: "TM30",
    249: "TM31", 250: "TM32", 251: "TM33", 252: "TM34", 253: "TM35",
    254: "TM36", 255: "TM37", 256: "TM38", 257: "TM39", 258: "TM40",
    259: "TM41", 260: "TM42", 261: "TM43", 262: "TM44", 263: "TM45",
    264: "TM46", 265: "TM47", 266: "TM48", 267: "TM49", 268: "TM50",
    289: "HM01", 290: "HM02", 291: "HM03", 292: "HM04", 293: "HM05",
    294: "HM06", 295: "HM07", 296: "HM08",
}

# Map names for FireRed (subset - Kanto region)
MAP_NAMES_FIRERED = {
    # Pallet Town area
    (0, 0): "Pallet Town",
    (0, 1): "Viridian City",
    (0, 2): "Pewter City",
    (0, 3): "Cerulean City",
    (0, 4): "Lavender Town",
    (0, 5): "Vermilion City",
    (0, 6): "Celadon City",
    (0, 7): "Fuchsia City",
    (0, 8): "Cinnabar Island",
    (0, 9): "Indigo Plateau",
    (0, 10): "Saffron City",
    (0, 12): "Route 1",
    (0, 13): "Route 2",
    (0, 14): "Route 3",
    (0, 15): "Route 4",
    (0, 16): "Route 5",
    (0, 17): "Route 6",
    (0, 18): "Route 7",
    (0, 19): "Route 8",
    (0, 20): "Route 9",
    (0, 21): "Route 10",
    (0, 22): "Route 11",
    (0, 23): "Route 12",
    (0, 24): "Route 13",
    (0, 25): "Route 14",
    (0, 26): "Route 15",
    (0, 27): "Route 16",
    (0, 28): "Route 17",
    (0, 29): "Route 18",
    (0, 30): "Route 19",
    (0, 31): "Route 20",
    (0, 32): "Route 21",
    (0, 33): "Route 22",
    (0, 34): "Route 23",
    (0, 35): "Route 24",
    (0, 36): "Route 25",
    # Buildings
    (1, 0): "Oak's Lab",
    (1, 1): "Viridian Gym",
    (1, 2): "Pewter Gym",
    (1, 3): "Cerulean Gym",
    (1, 4): "Vermilion Gym",
    (1, 5): "Celadon Gym",
    (1, 6): "Fuchsia Gym",
    (1, 7): "Saffron Gym",
    (1, 8): "Cinnabar Gym",
    (1, 9): "Viridian Forest",
    (1, 10): "Mt. Moon",
    (1, 11): "SS Anne",
    (1, 12): "Pokemon Tower",
    (1, 13): "Rocket Hideout",
    (1, 14): "Silph Co.",
    (1, 15): "Pokemon Mansion",
    (1, 16): "Victory Road",
    (1, 17): "Pokemon League",
    (1, 18): "Rock Tunnel",
    (1, 19): "Seafoam Islands",
    (1, 20): "Power Plant",
    (1, 21): "Diglett's Cave",
    (1, 22): "Pokemon Day Care",
}

# Badge names
BADGE_NAMES = [
    "Boulder Badge", "Cascade Badge", "Thunder Badge", "Rainbow Badge",
    "Soul Badge", "Marsh Badge", "Volcano Badge", "Earth Badge",
]

# Nature names
NATURE_NAMES = [
    "Hardy", "Lonely", "Brave", "Adamant", "Naughty",
    "Bold", "Docile", "Relaxed", "Impish", "Lax",
    "Timid", "Hasty", "Serious", "Jolly", "Naive",
    "Modest", "Mild", "Quiet", "Bashful", "Rash",
    "Calm", "Gentle", "Sassy", "Careful", "Quirky",
]


class FireRedMemoryReader(GameMemoryReader):
    """Memory reader for *Pokemon FireRed* (USA 1.0).

    Implements full Gen 3 Pokemon data decryption including
    substructure reordering and personality-based encryption.

    Parameters
    ----------
    emulator : Emulator
        A loaded :class:`~poke_player.emulator.PyGBAEmulator` running
        a FireRed ROM.
    """

    @property
    def game_name(self) -> str:
        return "Pokemon FireRed (USA)"

    # -- internal helpers --------------------------------------------------

    def _get_saveblock1(self) -> int:
        """Dereference the SaveBlock1 pointer."""
        return self.emu.read_u32(ADDR_SAVEBLOCK1_PTR)

    def _get_saveblock2(self) -> int:
        """Dereference the SaveBlock2 pointer."""
        return self.emu.read_u32(ADDR_SAVEBLOCK2_PTR)

    def _read_gen3_string(self, addr: int, max_len: int = 8) -> str:
        """Read a Gen 3 encoded string from memory."""
        raw = self.emu.read_range(addr, max_len)
        chars = []
        for b in raw:
            if b == 0xFF:  # Gen 3 terminator
                break
            chars.append(GEN3_ENCODING.get(b, "?"))
        return "".join(chars)

    def _decrypt_pokemon(self, data: bytes) -> Tuple[Dict[str, Any], bytes]:
        """Decrypt a 100-byte Pokemon data structure.

        Returns
        -------
        Tuple of (unencrypted_substructures_dict, raw_decrypted_block)
        """
        if len(data) != PARTY_MON_SIZE_GEN3:
            raise ValueError(f"Expected {PARTY_MON_SIZE_GEN3} bytes, got {len(data)}")

        # Parse header (unencrypted)
        personality = struct.unpack("<I", data[0:4])[0]
        ot_id = struct.unpack("<I", data[4:8])[0]
        nickname = self._decode_gen3_text(data[8:18])
        language = data[18]
        misc_flags = data[19]
        ot_name = self._decode_gen3_text(data[20:27])
        markings = data[27]
        checksum = struct.unpack("<H", data[28:30])[0]
        unknown = struct.unpack("<H", data[30:32])[0]

        # Decrypt the 48-byte block
        encryption_key = personality ^ ot_id
        encrypted_block = data[32:80]

        decrypted = bytearray(ENCRYPTED_BLOCK_SIZE)
        for i in range(0, ENCRYPTED_BLOCK_SIZE, 4):
            word = struct.unpack("<I", encrypted_block[i:i+4])[0]
            decrypted_word = word ^ encryption_key
            decrypted[i:i+4] = struct.pack("<I", decrypted_word)

        # Determine substructure order
        substructure_index = personality % 24
        order = SUBSTRUCTURE_ORDER[substructure_index]

        # Parse substructures
        substructures = {}
        for i, struct_type in enumerate(order):
            start = i * SUBSTRUCTURE_SIZE
            end = start + SUBSTRUCTURE_SIZE
            substructures[struct_type] = bytes(decrypted[start:end])

        return {
            "personality_value": personality,
            "ot_id": ot_id,
            "nickname": nickname,
            "language": language,
            "misc_flags": misc_flags,
            "ot_name": ot_name,
            "markings": markings,
            "checksum": checksum,
            "substructures": substructures,
            "substructure_order": order,
        }, bytes(decrypted)

    def _decode_gen3_text(self, data: bytes) -> str:
        """Decode Gen 3 text encoding."""
        chars = []
        for b in data:
            if b == 0xFF:
                break
            chars.append(GEN3_ENCODING.get(b, "?"))
        return "".join(chars)

    def _parse_growth_substructure(self, data: bytes) -> Dict[str, Any]:
        """Parse Growth substructure (G)."""
        return {
            "species": struct.unpack("<H", data[0:2])[0],
            "item_held": struct.unpack("<H", data[2:4])[0],
            "experience": struct.unpack("<I", data[4:8])[0],
            "pp_bonuses": data[8],
            "friendship": data[9],
            "unknown": data[10:12],
        }

    def _parse_attacks_substructure(self, data: bytes) -> Dict[str, Any]:
        """Parse Attacks substructure (A)."""
        return {
            "moves": [
                struct.unpack("<H", data[0:2])[0],
                struct.unpack("<H", data[2:4])[0],
                struct.unpack("<H", data[4:6])[0],
                struct.unpack("<H", data[6:8])[0],
            ],
            "pp": [
                data[8], data[9], data[10], data[11]
            ],
        }

    def _parse_evs_condition_substructure(self, data: bytes) -> Dict[str, Any]:
        """Parse EVs & Condition substructure (E)."""
        return {
            "hp_ev": data[0],
            "attack_ev": data[1],
            "defense_ev": data[2],
            "speed_ev": data[3],
            "sp_attack_ev": data[4],
            "sp_defense_ev": data[5],
            "coolness": data[6],
            "beauty": data[7],
            "cuteness": data[8],
            "smartness": data[9],
            "toughness": data[10],
            "feel": data[11],
        }

    def _parse_misc_substructure(self, data: bytes) -> Dict[str, Any]:
        """Parse Misc substructure (M)."""
        return {
            "pokerus_status": data[0],
            "met_location": data[1],
            "origins_info": struct.unpack("<H", data[2:4])[0],
            "ivs": struct.unpack("<I", data[4:8])[0],  # 30 bits for 6 IVs
            "ribbons_obedience": struct.unpack("<I", data[8:12])[0],
        }

    def _parse_ivs(self, iv_dword: int) -> Dict[str, int]:
        """Parse IVs from 32-bit value."""
        return {
            "hp": (iv_dword >> 0) & 0x1F,
            "attack": (iv_dword >> 5) & 0x1F,
            "defense": (iv_dword >> 10) & 0x1F,
            "speed": (iv_dword >> 15) & 0x1F,
            "sp_attack": (iv_dword >> 20) & 0x1F,
            "sp_defense": (iv_dword >> 25) & 0x1F,
        }

    def _get_nature(self, personality: int) -> str:
        """Get nature from personality value."""
        return NATURE_NAMES[personality % 25]

    def _get_ability(self, personality: int, species: int) -> str:
        """Determine ability (Gen 3 has 2 abilities per species)."""
        ability_num = (personality >> 31) & 1
        return f"Ability {ability_num + 1}"

    def _get_gender(self, personality: int, species: int) -> str:
        """Determine gender based on personality value and species ratio."""
        # Simplified - would need gender ratio lookup per species
        # For now, use personality value threshold
        threshold = (personality & 0xFF)
        if threshold < 31:
            return "Female"
        elif threshold > 225:
            return "Genderless"
        else:
            return "Male"

    def _get_shininess(self, personality: int, ot_id: int) -> bool:
        """Determine if Pokemon is shiny."""
        tid = ot_id & 0xFFFF
        sid = (ot_id >> 16) & 0xFFFF
        p1 = personality & 0xFFFF
        p2 = (personality >> 16) & 0xFFFF
        return (tid ^ sid ^ p1 ^ p2) < 8

    # -- public interface --------------------------------------------------

    def read_player(self) -> Dict[str, Any]:
        """Read player data including name, money, time, and badges."""
        sb1 = self._get_saveblock1()
        sb2 = self._get_saveblock2()

        # Player name (8 bytes from SaveBlock2)
        name = self._read_gen3_string(sb2 + OFF_PLAYER_NAME, 8)

        # Trainer ID (4 bytes: TID + SID)
        trainer_id = self.emu.read_u32(sb2 + OFF_TRAINER_ID)
        tid = trainer_id & 0xFFFF
        sid = (trainer_id >> 16) & 0xFFFF

        # Gender
        gender_code = self.emu.read_u8(sb2 + OFF_PLAYER_GENDER)
        gender = "Male" if gender_code == 0 else "Female"

        # Play time
        hours = self.emu.read_u16(sb2 + OFF_PLAY_TIME)
        minutes = self.emu.read_u8(sb2 + OFF_PLAY_TIME + 2)
        seconds = self.emu.read_u8(sb2 + OFF_PLAY_TIME + 3)

        # Money (encrypted with security key from SaveBlock2)
        security_key = self.emu.read_u32(sb2 + 0x00F20)
        encrypted_money = self.emu.read_u32(sb1 + OFF_MONEY)
        money = encrypted_money ^ security_key

        # Badges
        badges = self.emu.read_u16(sb2 + OFF_BADGES)
        badge_list = [
            BADGE_NAMES[i] for i in range(8) if badges & (1 << i)
        ]

        return {
            "name": name,
            "trainer_id": tid,
            "secret_id": sid,
            "gender": gender,
            "money": money,
            "play_time": {
                "hours": hours,
                "minutes": minutes,
                "seconds": seconds,
            },
            "badges": badge_list,
            "badge_count": len(badge_list),
        }

    def read_party(self) -> List[Dict[str, Any]]:
        """Read party Pokemon with full Gen 3 decryption."""
        sb1 = self._get_saveblock1()

        party_count = self.emu.read_u8(sb1 + OFF_PARTY_COUNT)
        if party_count == 0:
            return []

        party = []
        for i in range(min(party_count, 6)):
            addr = sb1 + OFF_PARTY_DATA + (i * PARTY_MON_SIZE_GEN3)
            raw = bytes(self.emu.read_range(addr, PARTY_MON_SIZE_GEN3))

            try:
                decrypted_info, _ = self._decrypt_pokemon(raw)
                subs = decrypted_info["substructures"]

                # Parse substructures
                growth = self._parse_growth_substructure(subs.get("G", b"\x00" * 12))
                attacks = self._parse_attacks_substructure(subs.get("A", b"\x00" * 12))
                evs = self._parse_evs_condition_substructure(subs.get("E", b"\x00" * 12))
                misc = self._parse_misc_substructure(subs.get("M", b"\x00" * 12))
                ivs = self._parse_ivs(misc["ivs"])

                species_id = growth["species"]
                personality = decrypted_info["personality_value"]
                ot_id = decrypted_info["ot_id"]

                pokemon = {
                    "slot": i + 1,
                    "species_id": species_id,
                    "species": SPECIES_NAMES.get(species_id, f"Unknown ({species_id})"),
                    "nickname": decrypted_info["nickname"],
                    "level": self._calculate_level(growth["experience"], species_id),
                    "experience": growth["experience"],
                    "personality_value": personality,
                    "nature": self._get_nature(personality),
                    "ability": self._get_ability(personality, species_id),
                    "gender": self._get_gender(personality, species_id),
                    "shiny": self._get_shininess(personality, ot_id),
                    "item_held": ITEM_NAMES.get(growth["item_held"], f"Item {growth['item_held']}"),
                    "friendship": growth["friendship"],
                    "moves": [
                        ITEM_NAMES.get(m, f"Move {m}") for m in attacks["moves"] if m != 0
                    ],
                    "pp": attacks["pp"],
                    "ivs": ivs,
                    "evs": {
                        "hp": evs["hp_ev"],
                        "attack": evs["attack_ev"],
                        "defense": evs["defense_ev"],
                        "speed": evs["speed_ev"],
                        "sp_attack": evs["sp_attack_ev"],
                        "sp_defense": evs["sp_defense_ev"],
                    },
                    "stats": self._calculate_stats(species_id, ivs, evs, personality),
                    "condition": {
                        "coolness": evs["coolness"],
                        "beauty": evs["beauty"],
                        "cuteness": evs["cuteness"],
                        "smartness": evs["smartness"],
                        "toughness": evs["toughness"],
                    },
                    "met_location": misc["met_location"],
                    "pokerus": misc["pokerus_status"],
                    "markings": decrypted_info["markings"],
                    "ot_name": decrypted_info["ot_name"],
                    "ot_id": ot_id & 0xFFFF,
                }
                party.append(pokemon)
            except Exception as e:
                # If decryption fails, return basic info
                party.append({
                    "slot": i + 1,
                    "error": str(e),
                    "raw_data": raw.hex(),
                })

        return party

    def _calculate_level(self, experience: int, species_id: int) -> int:
        """Calculate level from experience (simplified - uses medium-fast formula)."""
        # Medium-fast growth rate: EXP = n^3
        import math
        level = int(round(experience ** (1/3)))
        return min(max(level, 1), 100)

    def _calculate_stats(self, species_id: int, ivs: Dict[str, int],
                         evs: Dict[str, int], personality: int) -> Dict[str, int]:
        """Calculate stats from base stats, IVs, EVs, and level.

        Note: This is a simplified calculation without base stats lookup.
        """
        # Simplified - would need base stats table per species
        level = 50  # Placeholder
        return {
            "hp": 0,  # Would calculate from base_hp
            "attack": 0,
            "defense": 0,
            "speed": 0,
            "sp_attack": 0,
            "sp_defense": 0,
        }

    def read_bag(self) -> List[Dict[str, Any]]:
        """Read bag contents."""
        sb1 = self._get_saveblock1()

        # Bag items are stored as (item_id, quantity) pairs
        # Max 30 items in FireRed
        bag_items = []
        for i in range(30):
            addr = sb1 + OFF_BAG_ITEMS + (i * 4)
            item_id = self.emu.read_u16(addr)
            quantity = self.emu.read_u16(addr + 2)

            if item_id == 0:
                break

            bag_items.append({
                "item": ITEM_NAMES.get(item_id, f"Item {item_id}"),
                "item_id": item_id,
                "quantity": quantity,
            })

        return bag_items

    def read_battle(self) -> Dict[str, Any]:
        """Read battle state.

        Note: Battle state in Gen 3 is more complex and varies by situation.
        This provides basic detection.
        """
        # Battle type indicator (simplified - actual addresses vary)
        # In FireRed, battle state is typically in EWRAM around 0x02000000
        # This is a simplified implementation
        try:
            # Try to read from common battle state addresses
            battle_flags = self.emu.read_u8(0x02022B4C)  # gBattleTypeFlags
            in_battle = battle_flags != 0

            if in_battle:
                return {
                    "in_battle": True,
                    "battle_type": self._get_battle_type(battle_flags),
                    "turn_number": self.emu.read_u16(0x02022B4E),
                }
        except Exception:
            pass

        return {
            "in_battle": False,
            "battle_type": None,
        }

    def _get_battle_type(self, flags: int) -> str:
        """Determine battle type from flags."""
        battle_types = {
            0x01: "Wild",
            0x02: "Trainer",
            0x04: "Double",
            0x08: "Link",
            0x10: "Multi",
            0x20: "Safari",
            0x40: "Battle Tower",
        }

        types = []
        for flag, name in battle_types.items():
            if flags & flag:
                types.append(name)

        return " + ".join(types) if types else "Unknown"

    def read_dialog(self) -> Dict[str, Any]:
        """Read dialog/text box state."""
        # Dialog state in Gen 3
        try:
            # gTextFlags or similar
            text_state = self.emu.read_u8(0x02020000)  # Approximate
            return {
                "active": text_state != 0,
                "text_state": text_state,
            }
        except Exception:
            return {
                "active": False,
                "text_state": 0,
            }

    def read_map_info(self) -> Dict[str, Any]:
        """Read current map information."""
        sb1 = self._get_saveblock1()

        map_group = self.emu.read_u8(sb1 + OFF_MAP_GROUP)
        map_number = self.emu.read_u8(sb1 + OFF_MAP_NUMBER)

        # Position (these offsets may need adjustment)
        pos_x = self.emu.read_u16(sb1 + OFF_POS_X)
        pos_y = self.emu.read_u16(sb1 + OFF_POS_Y)

        map_key = (map_group, map_number)
        map_name = MAP_NAMES_FIRERED.get(map_key, f"Map {map_group}-{map_number}")

        return {
            "map_group": map_group,
            "map_number": map_number,
            "map_name": map_name,
            "position": {
                "x": pos_x,
                "y": pos_y,
            },
        }

    def read_flags(self) -> Dict[str, Any]:
        """Read key story and event flags."""
        sb2 = self._get_saveblock2()

        # Badges
        badges = self.emu.read_u16(sb2 + OFF_BADGES)
        badge_list = [
            BADGE_NAMES[i] for i in range(8) if badges & (1 << i)
        ]

        # Game state flags (simplified)
        # Would need to read from flag array in SaveBlock1
        try:
            # Pokedex obtained
            pokedex_flag = self.emu.read_u8(sb2 + 0x0019)
            has_pokedex = bool(pokedex_flag & 0x01)
        except Exception:
            has_pokedex = False

        return {
            "badges": badge_list,
            "badge_count": len(badge_list),
            "has_pokedex": has_pokedex,
        }


# Alias for consistency
PokemonFireRedReader = FireRedMemoryReader
