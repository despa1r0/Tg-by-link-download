from bot.services.providers.common import hostname_matches

INSTAGRAM_DOMAINS = ("instagram.com", "instagr.am")


def is_instagram_url(url: str) -> bool:
    return hostname_matches(url, INSTAGRAM_DOMAINS)
