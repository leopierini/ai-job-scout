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
