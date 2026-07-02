"""Pixel art cat sprite data — 16x16 frames, each char = color index."""

SPRITE_SIZE = 16
SCALE = 4  # displayed at 64x64

# Color palette (indexed by character)
PALETTE = {
    ".": None,           # transparent
    "B": "#FF8C42",      # body orange
    "b": "#FFA563",      # light orange (highlight)
    "D": "#D4692B",      # dark outline
    "d": "#C05A20",      # darker shadow
    "W": "#FFFFFF",      # white
    "K": "#2D2D2D",      # dark pupils
    "P": "#FFB5B5",      # pink cheeks
    "p": "#FF8888",      # dark pink (mouth)
    "Y": "#FFE066",      # yellow (sparkle)
    "G": "#88CC88",      # green (sparkle for happy)
}

# Each sprite is a list of 16 strings, each 16 chars
# . = transparent, letters = colors from PALETTE

IDLE_1 = [
    "................",
    "....DD....DD....",
    "...DBBD..DBBD...",
    "...DBBD..DBBD...",
    "....DD....DD....",
    "...DDDDDDDDDD...",
    "..DBBBBBBBBBBD..",
    ".DBBBBW..WBBBBD.",
    ".DBBBBWK.WKBBBD.",
    ".DBBBBB..PBBBD..",
    ".DBBBBBBBBBBBD..",
    "..DDDDDDDDDDDD..",
    "...DDD....DDD...",
    "...DDB.BB.DD....",
    "...DDD.BB.DD....",
    "...DDD.BB.DD....",
]

IDLE_2 = [
    "................",
    "....DD....DD....",
    "...DBBD..DBBD...",
    "...DBBD..DBBD...",
    "....DD....DD....",
    "...DDDDDDDDDD...",
    "..DBBBBBBBBBBD..",
    ".DBBBBW..WBBBBD.",
    ".DBBBBWK.WKBBBD.",
    ".DBBBBB..PBBBD..",
    ".DBBBBBBBBBBBD..",
    "..DDDDDDDDDDDD..",
    "...DDD....DDD...",
    "...DDD.BB.DD....",
    "...DDD.BB.DD....",
    "...DDD....DD....",
]

WALK_1 = [
    "................",
    "....DD....DD....",
    "...DBBD..DBBD...",
    "...DBBD..DBBD...",
    "....DD....DD....",
    "...DDDDDDDDDD...",
    "..DBBBBBBBBBBD..",
    ".DBBBBW..WBBBBD.",
    ".DBBBBWK.WKBBBD.",
    ".DBBBBB..PBBBD..",
    ".DBBBBBBBBBBBD..",
    "..DDDDDDDDDDDD..",
    "...DDD....DDD...",
    "...DDB.BB.DDD...",
    "...DDD.BB........",
    "...DDD..BB.......",
]

WALK_2 = [
    "................",
    "....DD....DD....",
    "...DBBD..DBBD...",
    "...DBBD..DBBD...",
    "....DD....DD....",
    "...DDDDDDDDDD...",
    "..DBBBBBBBBBBD..",
    ".DBBBBW..WBBBBD.",
    ".DBBBBWK.WKBBBD.",
    ".DBBBBB..PBBBD..",
    ".DBBBBBBBBBBBD..",
    "..DDDDDDDDDDDD..",
    "...DDD....DDD...",
    "........BB.DD...",
    ".......BB.DDD...",
    "......BB..DDD...",
]

THINK_1 = [
    "................",
    "....DD....DD....",
    "...DBBD..DBBD...",
    "...DBBD..DBBD...",
    "....DD....DD....",
    "...DDDDDDDDDD...",
    "..DBBBBBBBBBBD..",
    ".DBBBBW..WBBBBD.",
    ".DBBBBWK.WKBBBD.",
    ".DBBBBB..BBBDD..",
    ".DBBBBBBBBBBDD..",
    "..DDDDDDDDDDDD..",
    "...DDD....DDD...",
    "...DDB.BB.DDD...",
    "...DDD.BB........",
    "...DDD............",
]

THINK_2 = [
    "..........YY....",
    "....DD..YYYDD...",
    "...DBBD..DBBD...",
    "...DBBD..DBBD.Y.",
    "....DD....DD....",
    "...DDDDDDDDDD...",
    "..DBBBBBBBBBBD..",
    ".DBBBBW..WBBBBD.",
    ".DBBBBWK.WKBBBD.",
    ".DBBBBB...BBBD..",
    ".DBBBBBBBBBBBD..",
    "..DDDDDDDDDDDD..",
    "...DDD....DDD...",
    "...DDB.BB.DDD...",
    "...DDD....DDD...",
    "...DDD....DDD...",
]

HAPPY_1 = [
    "................",
    "...YY...........",
    "....DD.YY.DD....",
    "...DBBDYYDBBD...",
    "...DBBD..DBBD...",
    "....DD....DD....",
    "...DDDDDDDDDD...",
    "..DBBBBBBBBBBD..",
    ".DBBBBW..WBBBBD.",
    ".DBBBBWW...BBBD.",
    ".DBBBBBpPBBBDD..",
    ".DBBBBBBBBBBBD..",
    "..DDDDDDDDDDDD..",
    "...DDD....DDD...",
    "...DDD....DDD...",
    "...DDD....DDD...",
]

HAPPY_2 = [
    "........YY......",
    "...YY...YY......",
    "....DD....DD....",
    "...DBBD..DBBD...",
    "...DBBD..DBBD...",
    "....DD....DD....",
    "...DDDDDDDDDD...",
    "..DBBBBBBBBBBD..",
    ".DBBBBW..WBBBBD.",
    ".DBBBB.WW.WBBBD.",
    ".DBBBBBP.PBBBD..",
    ".DBBBBBBBBBBBD..",
    "..DDDDDDDDDDDD..",
    "...DDD....DDD...",
    "...DDD....DDD...",
    "...DDD....DDD...",
]

SLEEP_1 = [
    "................",
    "....DD....DD....",
    "...DBBD..DBBD...",
    "...DBBD..DBBD...",
    "....DD....DD....",
    "...DDDDDDDDDD...",
    "..DBBBBBBBBBBD..",
    ".DBBBB....BBBBD.",
    ".DBBBB....BBBBD.",
    ".DBBBBB..PBBBD..",
    ".DBBBBBBBBBBBD..",
    "..DDDDDDDDDDDD..",
    "...DDD....DDD...",
    "...DDB.BB.DDD...",
    "...DDD.BB.DDD...",
    "...DDD....DDD...",
]

SLEEP_2 = [
    "................",
    "....DD....DD....",
    "...DBBD..DBBD...",
    "...DBBD..DBBD...",
    "....DD....DD....",
    "...DDDDDDDDDD...",
    "..DBBBBBBBBBBD..",
    ".DBBBB..K.BBBBD.",
    ".DBBBB....BBBBD.",
    ".DBBBBB..PBBBD..",
    ".DBBBBBBBBBBBD..",
    "..DDDDDDDDDDDD..",
    "...DDD....DDD...",
    "...DDB.BB.DDD...",
    "...DDD.BB.DDD...",
    "...DDD....DDD...",
]

# --- Mood-based idle variants ---

# Happy idle (mood >= 70): sparkle eyes, big smile, perky ears
IDLE_HAPPY_1 = [
    "................",
    "....DD....DD....",
    "...DBBD..DBBD...",
    "...DBBD..DBBD...",
    "....DD....DD....",
    "...DDDDDDDDDD...",
    "..DBBBBBBBBBBD..",
    ".DBBBBW..WBBBBD.",
    ".DBBBBWK.WKBBBD.",
    ".DBBBBB..PBBBD..",
    ".DBBBBBp.BBBBBD.",
    ".DBBBBBBBBBBBD..",
    "..DDDDDDDDDDDD..",
    "...DDD.YY.DDD...",
    "...DDD.YY.DDD...",
    "...DDD....DDD...",
]

IDLE_HAPPY_2 = [
    "................",
    "....DD....DD....",
    "...DBBD..DBBD...",
    "...DBBD..DBBD...",
    "....DD....DD....",
    "...DDDDDDDDDD...",
    "..DBBBBBBBBBBD..",
    ".DBBBBW..WBBBBD.",
    ".DBBBBWK.WKBBBD.",
    ".DBBBBB..PBBBD..",
    ".DBBBBB.PPBBBD..",
    ".DBBBBBBBBBBBD..",
    "..DDDDDDDDDDDD..",
    "...DDD.YY..YY....",
    "...DDD..YY......",
    "...DDD....DDD...",
]

# Sad idle (mood < 40): droopy ears, sad eyes, small mouth
IDLE_SAD_1 = [
    "................",
    "......DD....DD..",
    ".....DBBD..DBBD.",
    ".....DBBD..DBBD.",
    "......DD....DD..",
    ".....DDDDDDDDD..",
    "....DBBBBBBBBD..",
    "...DBBBWK.WKBBBD.",
    "...DBBBBW..BBBBD.",
    "....DBBBB.PBBBD..",
    "....DBBBBBBBBD..",
    ".....DDDDDDDDD..",
    "......DDD..DDD..",
    "......DD.BB..DD..",
    "......DD.BB..DD..",
    "......DDD..DDD..",
]

IDLE_SAD_2 = [
    "................",
    "......DD....DD..",
    ".....DBBD..DBBD.",
    ".....DBBD..DBBD.",
    "......DD....DD..",
    ".....DDDDDDDDD..",
    "....DBBBBBBBBD..",
    "...DBBBWK.WKBBBD.",
    "...DBBBBW..BBBBD.",
    "....DBBBB.PBBBD..",
    "....DBBBBBBBBD..",
    ".....DDDDDDDDD..",
    "......DDD..DDD..",
    "......DDBB...DD..",
    "......DD.BB..DD..",
    "......DDD..DDD..",
]

# Sad idle blink variant
IDLE_SAD_BLINK = [
    "................",
    "......DD....DD..",
    ".....DBBD..DBBD.",
    ".....DBBD..DBBD.",
    "......DD....DD..",
    ".....DDDDDDDDD..",
    "....DBBBBBBBBD..",
    "...DBBB....BBBD.",
    "...DBBB....BBBD.",
    "....DBBBB.PBBBD..",
    "....DBBBBBBBBD..",
    ".....DDDDDDDDD..",
    "......DDD..DDD..",
    "......DD.BB..DD..",
    "......DD.BB..DD..",
    "......DDD..DDD..",
]

# Animation sequences: name -> (frames[], interval_ms)
ANIMATIONS = {
    "idle":       ([IDLE_1, IDLE_2], 500),
    "idle_happy": ([IDLE_HAPPY_1, IDLE_HAPPY_2], 500),
    "idle_sad":   ([IDLE_SAD_1, IDLE_SAD_2], 600),
    "walk":       ([WALK_1, WALK_2], 200),
    "think":      ([THINK_1, THINK_2], 400),
    "happy":      ([HAPPY_1, HAPPY_2], 300),
    "sleep":      ([SLEEP_1, SLEEP_2], 800),
    "idle_blink":       ([IDLE_1, IDLE_1, IDLE_1, SLEEP_1, SLEEP_1, IDLE_1], 150),
    "idle_blink_happy": ([IDLE_HAPPY_1, IDLE_HAPPY_1, IDLE_HAPPY_1, SLEEP_1, SLEEP_1, IDLE_HAPPY_1], 150),
    "idle_blink_sad":   ([IDLE_SAD_1, IDLE_SAD_1, IDLE_SAD_1, IDLE_SAD_BLINK, IDLE_SAD_BLINK, IDLE_SAD_1], 200),
}
