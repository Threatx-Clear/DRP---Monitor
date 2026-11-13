import asyncio
import json
import logging
import re
import sys
from pathlib import Path

import maigret
from maigret import search as maigret_search
from maigret.sites import MaigretDatabase


SITES = ("Instagram", "LinkedIn", "TikTok", "Twitter", "YouTube", "Telegram")
TIMEOUT = 8
MAX_CONNECTIONS = 3
MAX_USERNAMES = 30


async def _search_usernames(usernames: list[str]) -> list[dict[str, str]]:
    database_path = Path(maigret.__file__).resolve().parent / "resources" / "data.json"
    database = MaigretDatabase().load_from_path(str(database_path))
    ranked_sites = database.ranked_sites_dict(names=list(SITES))
    site_dict = {site: ranked_sites[site] for site in SITES if site in ranked_sites}
    if not site_dict:
        raise RuntimeError("Maigret não encontrou os sites públicos configurados.")

    results_found = []
    logger = logging.getLogger("maigret")
    for username in usernames:
        results = await maigret_search(
            username=username,
            site_dict=site_dict,
            logger=logger,
            timeout=TIMEOUT,
            is_parsing_enabled=False,
            no_progressbar=True,
            retries=0,
            max_connections=MAX_CONNECTIONS,
        )
        for site, result in results.items():
            if result["status"].is_found():
                results_found.append({"site": site, "url": result["url_user"]})
    return results_found


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.CRITICAL)
    usernames = sys.argv[1:]
    if not usernames or len(usernames) > MAX_USERNAMES:
        raise SystemExit(f"Informe de 1 a {MAX_USERNAMES} usernames.")
    if any(
        not re.fullmatch(r"[A-Za-z0-9._-]{2,30}", username)
        for username in usernames
    ):
        raise SystemExit("A lista de usernames candidatos é inválida.")

    print(json.dumps(asyncio.run(_search_usernames(usernames))))


if __name__ == "__main__":
    main()
