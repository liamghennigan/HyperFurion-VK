"""Spoken commands and punctuation in other languages ([flow] language).

A pack adds its phrases on top of the English defaults (the commands of a
dictation in Spanish are Spanish, but "period" said in passing still
works); [flow.commands] and [flow.punctuation] still override both. Only
punctuation and the core layout commands: the prose folds (numbers,
dates, units) stay English.
"""

# punctuation phrase -> (glyph, mode, sentence_end), as DEFAULT_PUNCTUATION
_ES_PUNCT = {
    "punto": (".", "left", True), "punto y seguido": (".", "left", True),
    "punto final": (".", "left", True), "coma": (",", "left", False),
    "dos puntos": (":", "left", False), "punto y coma": (";", "left", False),
    "puntos suspensivos": ("...", "left", False),
    "abre interrogación": ("¿", "right", False), "abrir interrogación": ("¿", "right", False),
    "cierra interrogación": ("?", "left", True), "cerrar interrogación": ("?", "left", True),
    "signo de interrogación": ("?", "left", True),
    "abre exclamación": ("¡", "right", False), "abrir exclamación": ("¡", "right", False),
    "cierra exclamación": ("!", "left", True), "cerrar exclamación": ("!", "left", True),
    "abre comillas": ('"', "right", False), "cierra comillas": ('"', "left", False),
    "abre paréntesis": ("(", "right", False), "cierra paréntesis": (")", "left", False),
    "guion": ("-", "none", False),
}
_FR_PUNCT = {
    "point": (".", "left", True), "virgule": (",", "left", False),
    "deux points": (":", "left", False), "point virgule": (";", "left", False),
    "point d'interrogation": ("?", "left", True), "point d'exclamation": ("!", "left", True),
    "points de suspension": ("...", "left", False),
    "ouvrez les guillemets": ('"', "right", False), "fermez les guillemets": ('"', "left", False),
    "ouvrez la parenthèse": ("(", "right", False), "fermez la parenthèse": (")", "left", False),
    "tiret": ("-", "none", False),
}
_DE_PUNCT = {
    "punkt": (".", "left", True), "komma": (",", "left", False),
    "doppelpunkt": (":", "left", False), "semikolon": (";", "left", False),
    "strichpunkt": (";", "left", False), "fragezeichen": ("?", "left", True),
    "ausrufezeichen": ("!", "left", True), "auslassungspunkte": ("...", "left", False),
    "anführungszeichen auf": ('"', "right", False), "anführungszeichen zu": ('"', "left", False),
    "klammer auf": ("(", "right", False), "klammer zu": (")", "left", False),
    "bindestrich": ("-", "both", False),
}

LANGUAGE_PACKS: dict[str, dict] = {
    "en": {"commands": {}, "punctuation": {}},
    "es": {
        "commands": {
            "new_line": ("nueva línea", "nueva linea", "salto de línea", "salto de linea"),
            "new_paragraph": ("nuevo párrafo", "nuevo parrafo"),
            "scratch_that": ("borra eso", "borrar eso"),
        },
        "punctuation": _ES_PUNCT,
    },
    "fr": {
        "commands": {
            "new_line": ("à la ligne", "a la ligne", "nouvelle ligne"),
            "new_paragraph": ("nouveau paragraphe",),
            "scratch_that": ("efface ça", "efface ca", "annule ça", "annule ca"),
        },
        "punctuation": _FR_PUNCT,
    },
    "de": {
        "commands": {
            "new_line": ("neue zeile",),
            "new_paragraph": ("neuer absatz",),
            "scratch_that": ("streich das", "lösch das", "losch das"),
        },
        "punctuation": _DE_PUNCT,
    },
}

LANGUAGES = tuple(LANGUAGE_PACKS)
