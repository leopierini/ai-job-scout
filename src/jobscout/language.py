"""Guess the language a job posting is written in, from its most common short words."""

import re

from jobscout.normalize import fold

# Short, frequent words of each language, accents stripped (see `fold`). A posting often mixes in English
# job titles, so a guess needs many words and a clear lead before anything is dropped.
_WORDS: dict[str, frozenset[str]] = {
    code: frozenset(words.split())
    for code, words in {
        "en": "the and of to with for you your our we are is will be this that from have an or as at by "
        "who what about can all",
        "it": "il la le di del della delle dei e per con che un una sono nel nella gli ai alla anche come "
        "piu siamo nostro nostra nostri tuo tua si",
        "de": "der die das und mit fur wir sie ist ein eine du dein deine unser unsere bei von zu auf den dem "
        "des im oder sind werden auch nicht ihre",
        "fr": "le la les et des du pour avec vous nous est une un dans sur au aux votre vos notre nos sont qui "
        "que ou par ce cette",
        "nl": "de het een en van voor met je jij wij ons onze is zijn op te bij naar ook dat die niet als "
        "jouw wordt",
        "es": "el la los las y de del para con en un una es que por nuestro nuestra nuestros tu tus somos se "
        "al como mas",
        "pt": "o a os as e de do da dos das para com em um uma que por nosso nossa voce no na ao mais nao",
        "sv": "och att det som en ett ar for med pa av till vi du din ditt vara var har inte eller om hos",
        "da": "og at det som en et er for med pa af til vi du din dit vores har ikke eller om hos jeg",
        "no": "og at det som en et er for med pa av til vi du din ditt vare var har ikke eller om hos",
        "pl": "i w z na do sie jest oraz dla nie ze jak od po przez nasz nasza twoje",
    }.items()
}

LANGUAGES = tuple(_WORDS)
_WORD = re.compile(r"[^\W\d_]+")
MIN_WORDS = 12  # fewer hits than this for every language: too little text to judge
LEAD = 1.5  # the top language must have this many times the hits of the best language that is kept


def word_counts(text: str) -> dict[str, int]:
    """How many words of the text belong to each language's list."""
    counts = dict.fromkeys(_WORDS, 0)
    for word in _WORD.findall(fold(text)):
        for code, words in _WORDS.items():
            if word in words:
                counts[code] += 1
    return counts


def other_language(text: str, keep: frozenset[str]) -> str | None:
    """The language the text is clearly written in, if it is not one of `keep`; None when kept or unsure."""
    if not keep:
        return None
    counts = word_counts(text)
    top = max(counts, key=lambda code: counts[code])
    if top in keep or counts[top] < MIN_WORDS:
        return None
    best_kept = max((counts[code] for code in keep if code in counts), default=0)
    return top if counts[top] >= LEAD * best_kept else None
