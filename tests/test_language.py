from typing import get_args

from jobscout.language import LANGUAGES, MIN_WORDS, other_language, word_counts
from jobscout.settings import PostingLanguage

KEEP = frozenset({"en", "it"})

ENGLISH = (
    "We are looking for a product manager to join our team. You will work with designers and engineers to "
    "shape the roadmap of the product, and you will talk to the people who use it every week. This is a role "
    "for someone who is curious about users and can turn what they learn into clear decisions."
)
ITALIAN = (
    "Siamo alla ricerca di un product manager per il nostro team. Lavorerai con designer e sviluppatori per "
    "definire la roadmap del prodotto e parlerai con le persone che lo usano ogni settimana. Il ruolo è per chi "
    "ha curiosità per gli utenti e sa trasformare quello che impara in decisioni chiare, anche nella crescita."
)
GERMAN = (
    "Wir suchen eine Produktmanagerin oder einen Produktmanager für unser Team. Du arbeitest mit Design und "
    "Entwicklung an der Roadmap und sprichst jede Woche mit den Menschen, die das Produkt nutzen. Die Stelle ist "
    "für dich, wenn du neugierig auf Nutzer bist und aus dem, was du lernst, klare Entscheidungen machst."
)
FRENCH = (
    "Nous recherchons un product manager pour notre équipe. Vous travaillerez avec les designers et les "
    "développeurs sur la roadmap du produit et vous parlerez chaque semaine avec les personnes qui "
    "l'utilisent. Le poste est pour une personne curieuse qui sait transformer ce que les utilisateurs disent "
    "en décisions claires pour le produit et pour votre équipe."
)


def test_languages_match_the_setting_choices():
    assert set(LANGUAGES) == set(get_args(PostingLanguage))


def test_kept_languages_are_never_dropped():
    assert other_language(ENGLISH, KEEP) is None
    assert other_language(ITALIAN, KEEP) is None


def test_other_languages_are_named():
    assert other_language(GERMAN, KEEP) == "de"
    assert other_language(FRENCH, KEEP) == "fr"


def test_nothing_is_dropped_when_no_language_is_chosen():
    assert other_language(GERMAN, frozenset()) is None


def test_too_little_text_is_kept():
    short = "Produktmanager (m/w/d) in Berlin, mit Team und Roadmap"
    assert max(word_counts(short).values()) < MIN_WORDS
    assert other_language(short, KEEP) is None
    assert other_language("", KEEP) is None


def test_an_english_posting_with_a_german_title_and_footer_is_kept():
    mixed = "Produktmanager (m/w/d)\n" + ENGLISH + "\nWir freuen uns auf deine Bewerbung und dein Profil."
    assert other_language(mixed, KEEP) is None


def test_a_mostly_german_posting_with_english_terms_is_dropped():
    mixed = GERMAN + " Product Owner, Stakeholder Management, Scrum, Jira, Roadmap, OKRs."
    assert other_language(mixed, KEEP) == "de"


ITALIAN_BULLETS = """Cosa farai:
- Gestire i clienti e i fornitori
- Coordinare i rilasci da qui a fine anno
- Supporto al team commerciale a Milano o a Torino
Requisiti:
- Laurea in economia o ingegneria
- Esperienza in ambito SaaS
Cosa offriamo:
- Contratto a tempo indeterminato
- Sede a Bologna, raggiungibile in treno o in autobus
- Possibilità di crescita a breve o a medio termine"""

ENGLISH_BULLETS = """Responsibilities
- Run a discovery sprint every quarter
- Build a design system with a small team
- Own a roadmap and report on it
Requirements
- A degree in a related field
- 1-2 years in a product role
Perks
- Remote-first, flexible hours
- A learning budget"""

SPANISH = (
    "Buscamos un Product Manager para unirse a nuestro equipo en Madrid. Serás responsable de definir la "
    "estrategia del producto y de trabajar con los equipos de desarrollo y diseño. Ofrecemos un contrato "
    "indefinido, horario flexible y la posibilidad de teletrabajo para las personas del equipo."
)
PORTUGUESE = (
    "Estamos à procura de um Product Manager para a nossa equipa em Lisboa. Vais trabalhar com as equipas de "
    "engenharia e design para definir a estratégia do produto. Oferecemos contrato sem termo, seguro de saúde e "
    "um ambiente de trabalho flexível no centro da cidade, com mais tempo para você e para a sua família."
)


def test_bullet_postings_with_many_one_letter_words_are_kept():
    # "a", "e", "i" and "o" are frequent in Italian and English bullets, and also in Portuguese and Polish.
    assert other_language(ITALIAN_BULLETS, KEEP) is None
    assert other_language(ENGLISH_BULLETS, KEEP) is None
    assert other_language(ITALIAN_BULLETS, frozenset({"it"})) is None


def test_one_letter_words_are_not_counted():
    assert word_counts("a e i o y w z " * 20) == dict.fromkeys(LANGUAGES, 0)


def test_spanish_and_portuguese_are_named():
    assert other_language(SPANISH, KEEP) == "es"
    assert other_language(PORTUGUESE, KEEP) == "pt"
    assert other_language(SPANISH, frozenset({"en", "es"})) is None
