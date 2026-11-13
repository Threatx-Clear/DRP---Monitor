import ipaddress
import json
import re
import subprocess
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, quote_plus, urlsplit

import requests


CERTSPOTTER_URL = "https://api.certspotter.com/v1/issuances"
CRT_SH_URL = "https://crt.sh/"
URLSCAN_URL = "https://urlscan.io/api/v1/search/"
REQUEST_TIMEOUT = (5, 20)
USER_AGENT = "BrandResearchApp/1.0 (passive brand monitoring)"
MAX_TERMS = 5
MAX_DOMAINS = 2
MAX_RESULTS_PER_QUERY = 100
MAX_RECENT_CT_DOMAINS = 100
RECENT_CT_DAYS = 30
MAX_FINDINGS = 500
MAX_TYPO_VARIANTS = 96
MAX_DOMAINS_PER_URLSCAN_QUERY = 48
MAX_URLSCAN_QUERY_RESULTS = 100
MAX_PARALLEL_URLSCAN_QUERIES = 3
SHERLOCK_TIMEOUT = 120
MAIGRET_TIMEOUT = 240
MAX_SHERLOCK_USERNAMES = 10
SHERLOCK_SITES = (
    "Instagram",
    "LinkedIn",
    "TikTok",
    "Twitter",
    "YouTube",
    "Telegram",
    "mercadolivre",
)
SHERLOCK_PYTHON = (
    Path(__file__).resolve().parent / ".sherlock-venv" / "Scripts" / "python.exe"
    if sys.platform == "win32"
    else Path(sys.executable)
)


@dataclass(frozen=True)
class Finding:
    category: str
    source: str
    title: str
    url: str
    observed_at: str
    details: str


@dataclass
class SourceResult:
    name: str
    findings: list[Finding]
    error: str | None = None


def _raise_for_source_status(response: requests.Response, source: str) -> None:
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        wait_message = (
            f" Tente novamente após {retry_after}."
            if retry_after
            else " Aguarde antes de repetir a pesquisa."
        )
        raise ValueError(
            f"{source} atingiu o limite de consultas (HTTP 429).{wait_message}"
        )
    response.raise_for_status()


def _ascii_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z0-9]+", ascii_value)


def _brand_slugs(brand: str) -> list[str]:
    words = _ascii_words(brand)
    if not words:
        return []
    full_slug = "".join(words)
    core_words = words[1:] if len(words) > 1 and words[0] in {"o", "a", "os", "as"} else words
    core_slug = "".join(core_words)
    return list(dict.fromkeys([core_slug, full_slug]))


def infer_brand_terms(brand: str) -> list[str]:
    original = " ".join(brand.split())
    words = _ascii_words(original)
    if not original or not words:
        raise ValueError("Digite o nome da empresa usando letras ou números.")

    terms = [original, " ".join(words), *_brand_slugs(original)]
    unique_terms = []
    seen_terms = set()
    for term in terms:
        normalized_term = term.casefold()
        if normalized_term not in seen_terms:
            seen_terms.add(normalized_term)
            unique_terms.append(term)
    return unique_terms[:MAX_TERMS]


def infer_usernames(brand: str) -> list[str]:
    slugs = _brand_slugs(brand)
    if not slugs:
        return []
    candidates = [*slugs]
    for slug in slugs:
        candidates.extend(
            [
                f"{slug}oficial",
                f"oficial{slug}",
                f"{slug}br",
                f"{slug}brasil",
                f"oficial{slug}br",
            ]
        )
    return list(
        dict.fromkeys(
            candidate
            for candidate in candidates
            if re.fullmatch(r"[A-Za-z0-9._-]{2,30}", candidate)
        )
    )[:MAX_SHERLOCK_USERNAMES]


def infer_domain_candidates(brand: str) -> list[str]:
    slugs = _brand_slugs(brand)
    if not slugs:
        return []
    slug = slugs[0]
    return [f"{slug}.com.br", f"{slug}.com"]


def build_ad_library_findings(brand: str) -> list[Finding]:
    encoded_brand = quote_plus(brand)
    return [
        Finding(
            category="Anúncios — pesquisa na biblioteca",
            source="Meta Ad Library",
            title=f"Pesquisar “{brand}” na biblioteca da Meta",
            url=(
                "https://www.facebook.com/ads/library/"
                f"?active_status=all&ad_type=ALL&country=BR&q={encoded_brand}"
            ),
            observed_at="",
            details=(
                "Atalho de pesquisa com o nome da marca. A biblioteca abre em outra "
                "página; os anúncios não são importados automaticamente para este relatório."
            ),
        ),
        Finding(
            category="Anúncios — pesquisa na biblioteca",
            source="Google Ads Transparency Center",
            title=f"Pesquisar “{brand}” na biblioteca do Google",
            url=(
                "https://adstransparency.google.com/"
                f"?region=BR&query={encoded_brand}"
            ),
            observed_at="",
            details=(
                "Atalho de pesquisa com o nome da marca. A biblioteca abre em outra "
                "página; os anúncios não são importados automaticamente para este relatório."
            ),
        ),
    ]


def build_document_search_findings(brand: str) -> list[Finding]:
    encoded_brand = quote_plus(brand)
    pdf_query = quote_plus(f'"{brand}" filetype:pdf')
    return [
        Finding(
            category="Documentos — pesquisa manual",
            source="Google — PDFs",
            title=f"Pesquisar PDFs de “{brand}” no Google",
            url=f"https://www.google.com/search?q={pdf_query}",
            observed_at="",
            details=(
                "Atalho de pesquisa por PDFs indexados. Os documentos não são "
                "coletados nem importados automaticamente para este relatório."
            ),
        ),
        Finding(
            category="Documentos — pesquisa manual",
            source="Bing — PDFs",
            title=f"Pesquisar PDFs de “{brand}” no Bing",
            url=f"https://www.bing.com/search?q={pdf_query}",
            observed_at="",
            details=(
                "Atalho de pesquisa por PDFs indexados. Os documentos não são "
                "coletados nem importados automaticamente para este relatório."
            ),
        ),
        Finding(
            category="Documentos — pesquisa manual",
            source="Scribd",
            title=f"Pesquisar “{brand}” no Scribd",
            url=f"https://www.scribd.com/search?query={encoded_brand}",
            observed_at="",
            details=(
                "Atalho para a busca do Scribd. Os documentos não são coletados "
                "nem importados automaticamente para este relatório."
            ),
        ),
    ]


def infer_typosquat_candidates(
    brand: str, limit: int = MAX_TYPO_VARIANTS
) -> list[str]:
    slugs = _brand_slugs(brand)
    if not slugs or limit <= 0:
        return []
    original_slug = slugs[0]
    max_variants = min(limit, MAX_TYPO_VARIANTS)
    typo_slugs = _single_edit_typos(original_slug)
    seen = {original_slug, *typo_slugs}

    if len(typo_slugs) < max_variants:
        for first_variant in typo_slugs.copy():
            for candidate in _single_edit_typos(first_variant):
                if candidate in seen:
                    continue
                typo_slugs.append(candidate)
                seen.add(candidate)
                if len(typo_slugs) == max_variants:
                    break
            if len(typo_slugs) == max_variants:
                break

    return [
        f"{candidate}{suffix}"
        for candidate in typo_slugs[:max_variants]
        for suffix in (".com.br", ".com")
    ]


def _single_edit_typos(slug: str) -> list[str]:
    deletions = []
    transpositions = []
    duplications = []
    visual_substitutions = []
    keyboard_substitutions = []
    insertions = []

    for index in range(len(slug)):
        deletions.append(slug[:index] + slug[index + 1 :])
        duplications.append(slug[:index] + slug[index] + slug[index:])
        if index + 1 < len(slug):
            transpositions.append(
                slug[:index] + slug[index + 1] + slug[index] + slug[index + 2 :]
            )

    visual_map = {
        "a": "4",
        "b": "8",
        "e": "3",
        "i": "1",
        "l": "1",
        "o": "0",
        "s": "5",
        "t": "7",
        "z": "2",
    }
    for index, character in enumerate(slug):
        if character in visual_map:
            visual_substitutions.append(
                slug[:index] + visual_map[character] + slug[index + 1 :]
            )
        neighbors = _KEYBOARD_NEIGHBORS.get(character, "")
        for neighbor in neighbors:
            keyboard_substitutions.append(
                slug[:index] + neighbor + slug[index + 1 :]
            )
            insertions.append(slug[:index] + neighbor + slug[index:])
            insertions.append(slug[: index + 1] + neighbor + slug[index + 1 :])

    if "rn" in slug:
        visual_substitutions.append(slug.replace("rn", "m", 1))
    if "vv" in slug:
        visual_substitutions.append(slug.replace("vv", "w", 1))

    candidate_groups = (
        deletions,
        transpositions,
        duplications,
        visual_substitutions,
        keyboard_substitutions,
        insertions,
    )
    candidates = []
    for index in range(max(map(len, candidate_groups), default=0)):
        for group in candidate_groups:
            if index < len(group):
                candidates.append(group[index])

    deduplicated = list(
        dict.fromkeys(
            candidate
            for candidate in candidates
            if len(candidate) >= 3 and candidate != slug
        )
    )
    return deduplicated


_KEYBOARD_NEIGHBORS = {
    "q": "w",
    "w": "qe",
    "e": "wr",
    "r": "et",
    "t": "ry",
    "y": "tu",
    "u": "yi",
    "i": "uo",
    "o": "ip",
    "p": "o",
    "a": "s",
    "s": "ad",
    "d": "sf",
    "f": "dg",
    "g": "fh",
    "h": "gj",
    "j": "hk",
    "k": "jl",
    "l": "k",
    "z": "x",
    "x": "zc",
    "c": "xv",
    "v": "cb",
    "b": "vn",
    "n": "bm",
    "m": "n",
}


def normalize_domain(value: str) -> str:
    domain = value.strip().rstrip(".").lower()
    if "://" in domain or "/" in domain or ":" in domain:
        raise ValueError(f"Informe somente o domínio, sem protocolo ou caminho: {value}")
    try:
        domain = domain.encode("idna").decode("ascii")
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    except UnicodeError as error:
        raise ValueError(f"Domínio inválido: {value}") from error

    labels = domain.split(".")
    if (
        len(domain) > 253
        or len(labels) < 2
        or any(
            not label
            or len(label) > 63
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in labels
        )
    ):
        raise ValueError(f"Domínio inválido: {value}")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return domain
    raise ValueError(f"Use um domínio, não um endereço IP: {value}")


def validate_scope(terms: list[str], domains: list[str]) -> tuple[list[str], list[str]]:
    normalized_terms = list(dict.fromkeys(term.strip() for term in terms if term.strip()))
    normalized_domains = list(
        dict.fromkeys(normalize_domain(domain) for domain in domains if domain.strip())
    )
    if not normalized_terms:
        raise ValueError("Informe a marca ou ao menos um termo de busca.")
    if not normalized_domains:
        raise ValueError("Informe ao menos um domínio candidato.")
    if len(normalized_terms) > MAX_TERMS:
        raise ValueError(f"Use no máximo {MAX_TERMS} termos de busca.")
    if len(normalized_domains) > MAX_DOMAINS:
        raise ValueError(f"Use no máximo {MAX_DOMAINS} domínios candidatos.")
    return normalized_terms, normalized_domains


def _is_https_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _is_domain_or_subdomain(candidate: str, domains: list[str]) -> bool:
    candidate = candidate.lower().rstrip(".")
    return any(candidate == domain or candidate.endswith(f".{domain}") for domain in domains)


def _parse_certificate_date(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def search_recent_certificate_domains(brand: str) -> SourceResult:
    source_name = "crt.sh — certificados recentes"
    slugs = _brand_slugs(brand)
    if not slugs:
        return SourceResult(name=source_name, findings=[])

    slug = slugs[0]
    if len(slug) < 3:
        return SourceResult(
            name=source_name,
            findings=[],
            error="O termo precisa ter ao menos três caracteres para a busca no crt.sh.",
        )
    try:
        response = requests.get(
            CRT_SH_URL,
            params={
                "q": f"%{slug}%",
                "output": "json",
                "match": "ILIKE",
                "exclude": "expired",
                "deduplicate": "Y",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_source_status(response, "crt.sh")
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("a resposta do crt.sh não contém uma lista de certificados")

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=RECENT_CT_DAYS)
        recent_domains: dict[str, datetime] = {}
        for certificate in payload:
            if not isinstance(certificate, dict):
                continue
            issued_at = _parse_certificate_date(certificate.get("not_before"))
            if issued_at is None or not cutoff <= issued_at <= now:
                continue

            names = certificate.get("name_value")
            if not isinstance(names, str) or not names.strip():
                names = certificate.get("common_name")
            if not isinstance(names, str):
                continue
            for name in names.splitlines():
                candidate = name.strip().lower().removeprefix("*.").rstrip(".")
                if slug not in candidate:
                    continue
                try:
                    domain = normalize_domain(candidate)
                except ValueError:
                    continue
                previous_issue = recent_domains.get(domain)
                if previous_issue is None or issued_at > previous_issue:
                    recent_domains[domain] = issued_at

        findings = [
            Finding(
                category="Domínio com certificado recente — revisar",
                source="crt.sh — Certificate Transparency",
                title=domain,
                url=f"https://crt.sh/?q={quote(domain, safe='')}",
                observed_at=issued_at.isoformat().replace("+00:00", "Z"),
                details=(
                    f"Nome DNS listado em certificado com início de validade nos últimos "
                    f"{RECENT_CT_DAYS} dias. Isso não confirma a data de registro, "
                    "a propriedade do domínio ou fraude."
                ),
            )
            for domain, issued_at in sorted(
                recent_domains.items(), key=lambda item: item[1], reverse=True
            )[:MAX_RECENT_CT_DOMAINS]
        ]
        return SourceResult(name=source_name, findings=findings)
    except (requests.RequestException, ValueError) as error:
        return SourceResult(name=source_name, findings=[], error=str(error))


def _search_certificates(domain: str) -> SourceResult:
    source_name = f"Certificados públicos — candidato {domain}"
    try:
        response = requests.get(
            CERTSPOTTER_URL,
            params={
                "domain": domain,
                "include_subdomains": "true",
                "expand": "dns_names",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_source_status(response, "Cert Spotter")
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("a resposta de certificados não está no formato esperado")

        findings = []
        seen_names = set()
        for certificate in payload:
            if not isinstance(certificate, dict):
                continue
            names = certificate.get("dns_names", [])
            if not isinstance(names, list):
                continue
            for name in names:
                if not isinstance(name, str):
                    continue
                normalized_name = name.lower().removeprefix("*.").rstrip(".")
                if (
                    normalized_name in seen_names
                    or not _is_domain_or_subdomain(normalized_name, [domain])
                ):
                    continue
                seen_names.add(normalized_name)
                not_after = certificate.get("not_after") or "não informado"
                findings.append(
                    Finding(
                        category="Certificado — domínio candidato",
                        source="Cert Spotter — Certificate Transparency",
                        title=normalized_name,
                        url=f"https://crt.sh/?q={quote(normalized_name, safe='')}",
                        observed_at=certificate.get("not_before") or "",
                        details=(
                            f"Certificado público associado ao candidato {domain}; "
                            f"validade final: {not_after}. O domínio oficial não foi confirmado."
                        ),
                    )
                )
                if len(findings) >= MAX_RESULTS_PER_QUERY:
                    return SourceResult(name=source_name, findings=findings)
        return SourceResult(name=source_name, findings=findings)
    except (requests.RequestException, ValueError) as error:
        return SourceResult(name=source_name, findings=[], error=str(error))


def search_certificates(domains: list[str]) -> list[SourceResult]:
    if not domains:
        return []
    with ThreadPoolExecutor(max_workers=min(len(domains), MAX_DOMAINS)) as executor:
        return list(executor.map(_search_certificates, domains))


def _search_urlscan_term(term: str, candidate_domains: list[str]) -> SourceResult:
    source_name = f"URLScan — {term}"
    escaped_term = term.replace("\\", "\\\\").replace('"', '\\"')
    try:
        response = requests.get(
            URLSCAN_URL,
            params={
                "q": f'page.title:"{escaped_term}"',
                "size": MAX_RESULTS_PER_QUERY,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_source_status(response, "URLScan")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("a resposta do URLScan não contém resultados válidos")

        findings = []
        for result in payload["results"]:
            if not isinstance(result, dict):
                continue
            task = result.get("task")
            page = result.get("page")
            if not isinstance(task, dict) or not isinstance(page, dict):
                continue
            domain = page.get("domain") or task.get("domain")
            title = page.get("title")
            scan_id = task.get("uuid")
            if (
                not isinstance(domain, str)
                or not isinstance(title, str)
                or not isinstance(scan_id, str)
                or not re.fullmatch(r"[a-fA-F0-9-]{16,64}", scan_id)
                or _is_domain_or_subdomain(domain, candidate_domains)
            ):
                continue
            findings.append(
                Finding(
                    category="Site relacionado à marca — revisar",
                    source=f"URLScan — termo: {term}",
                    title=domain,
                    url=f"https://urlscan.io/result/{quote(scan_id, safe='')}/",
                    observed_at=task.get("time") or "",
                    details=(
                        f'Título observado: "{title}". '
                        "A correspondência não confirma phishing ou fraude."
                    ),
                )
            )
        return SourceResult(name=source_name, findings=findings)
    except (requests.RequestException, ValueError) as error:
        return SourceResult(name=source_name, findings=[], error=str(error))


def search_urlscan_domains(
    candidate_domains: list[str], typosquat_domains: list[str]
) -> SourceResult:
    source_name = "URLScan — domínios candidatos"
    all_domains = list(dict.fromkeys(candidate_domains + typosquat_domains))
    if not all_domains:
        return SourceResult(name=source_name, findings=[])
    batches = [
        all_domains[index : index + MAX_DOMAINS_PER_URLSCAN_QUERY]
        for index in range(0, len(all_domains), MAX_DOMAINS_PER_URLSCAN_QUERY)
    ]
    with ThreadPoolExecutor(
        max_workers=min(len(batches), MAX_PARALLEL_URLSCAN_QUERIES)
    ) as executor:
        results = list(
            executor.map(
                lambda batch: _search_urlscan_domain_batch(batch, typosquat_domains),
                batches,
            )
        )

    findings = []
    seen_domains = set()
    errors = []
    for result in results:
        errors.extend(result.error.split("; ") if result.error else [])
        for finding in result.findings:
            if finding.title in seen_domains or len(findings) >= MAX_FINDINGS:
                continue
            seen_domains.add(finding.title)
            findings.append(finding)
    return SourceResult(
        name=source_name,
        findings=findings,
        error="; ".join(errors) or None,
    )


def _search_urlscan_domain_batch(
    domains: list[str], typosquat_domains: list[str]
) -> SourceResult:
    source_name = f"URLScan — lote de {len(domains)} domínios"
    query = " OR ".join(f"page.domain:{domain}" for domain in domains)
    try:
        response = requests.get(
            URLSCAN_URL,
            params={"q": query, "size": MAX_URLSCAN_QUERY_RESULTS},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_source_status(response, "URLScan")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("a resposta do URLScan não contém resultados válidos")

        findings = []
        seen_domains = set()
        for result in payload["results"]:
            if not isinstance(result, dict):
                continue
            task = result.get("task")
            page = result.get("page")
            if not isinstance(task, dict) or not isinstance(page, dict):
                continue
            domain = page.get("domain") or task.get("domain")
            scan_id = task.get("uuid")
            if (
                not isinstance(domain, str)
                or not isinstance(scan_id, str)
                or not re.fullmatch(r"[a-fA-F0-9-]{16,64}", scan_id)
            ):
                continue
            matched_candidate = next(
                (
                    candidate
                    for candidate in domains
                    if _is_domain_or_subdomain(domain, [candidate])
                ),
                None,
            )
            if not matched_candidate or domain in seen_domains:
                continue
            seen_domains.add(domain)
            is_typosquat = matched_candidate in typosquat_domains
            findings.append(
                Finding(
                    category=(
                        "Possível typosquat — revisar"
                        if is_typosquat
                        else "Site em domínio candidato — revisar"
                    ),
                    source="URLScan — índice de varreduras públicas",
                    title=domain,
                    url=f"https://urlscan.io/result/{quote(scan_id, safe='')}/",
                    observed_at=task.get("time") or "",
                    details=(
                        f"Registro público observado para o candidato "
                        f"{matched_candidate}. A correspondência não confirma "
                        "propriedade, phishing ou fraude."
                    ),
                )
            )
        return SourceResult(name=source_name, findings=findings)
    except (requests.RequestException, ValueError) as error:
        return SourceResult(name=source_name, findings=[], error=str(error))


def search_public_sites(
    terms: list[str], candidate_domains: list[str]
) -> list[SourceResult]:
    if not terms:
        return []
    with ThreadPoolExecutor(
        max_workers=min(len(terms), MAX_PARALLEL_URLSCAN_QUERIES)
    ) as executor:
        results = list(
            executor.map(
                lambda term: _search_urlscan_term(term, candidate_domains),
                terms,
            )
        )

    for result in results:
        seen = set()
        deduplicated = []
        for finding in result.findings:
            if finding.url in seen:
                continue
            seen.add(finding.url)
            deduplicated.append(finding)
        result.findings = deduplicated
    return results


def _parse_sherlock_output(output: str) -> list[Finding]:
    findings = []
    for line in output.splitlines():
        match = re.match(r"^\[\+\]\s+([^:]+):\s+(https?://\S+)", line.strip())
        if not match:
            continue
        site, profile_url = match.groups()
        if not _is_https_url(profile_url):
            continue
        findings.append(
            Finding(
                category="Perfil público — username candidato",
                source=f"Sherlock — {site}",
                title=profile_url.rstrip("/").rsplit("/", 1)[-1],
                url=profile_url,
                observed_at="",
                details=(
                    "Sherlock reportou um perfil para um username inferido a partir "
                    "do nome da empresa. Isso não confirma vínculo ou oficialidade."
                ),
            )
        )
    return findings


def search_sherlock_usernames(usernames: list[str]) -> SourceResult:
    if not SHERLOCK_PYTHON.is_file():
        return SourceResult(
            name="Perfis — Sherlock",
            findings=[],
            error=(
                "Sherlock não está instalado neste ambiente. No Windows, execute "
                "setup_sherlock.ps1; no Streamlit Cloud, confira as dependências "
                "do requirements.txt e os logs de implantação."
            ),
        )
    if not usernames:
        return SourceResult(name="Perfis — Sherlock", findings=[])
    if len(usernames) > MAX_SHERLOCK_USERNAMES or any(
        not re.fullmatch(r"[A-Za-z0-9._-]{2,30}", username)
        for username in usernames
    ):
        return SourceResult(
            name="Perfis — Sherlock",
            findings=[],
            error="A lista de usernames candidatos é inválida.",
        )

    command = [
        str(SHERLOCK_PYTHON),
        "-m",
        "sherlock_project",
        *usernames,
        "--print-found",
        "--no-color",
        "--local",
        "--timeout",
        "12",
    ]
    for site in SHERLOCK_SITES:
        command.extend(["--site", site])

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=SHERLOCK_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return SourceResult(
            name="Perfis — Sherlock",
            findings=[],
            error=f"A consulta do Sherlock excedeu {SHERLOCK_TIMEOUT} segundos.",
        )
    except OSError as error:
        return SourceResult(name="Perfis — Sherlock", findings=[], error=str(error))

    findings = _parse_sherlock_output(completed.stdout)

    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip()
        return SourceResult(
            name="Perfis — Sherlock",
            findings=findings,
            error=error[:500] or f"Sherlock terminou com código {completed.returncode}.",
        )
    return SourceResult(name="Perfis — Sherlock", findings=findings)


def _parse_maigret_output(output: str) -> list[Finding]:
    records = json.loads(output)
    if not isinstance(records, list):
        raise ValueError("A saída do Maigret tem um formato inesperado.")

    findings = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("A saída do Maigret tem um formato inesperado.")
        site = record.get("site")
        profile_url = record.get("url")
        if not isinstance(site, str) or not isinstance(profile_url, str):
            raise ValueError("A saída do Maigret tem um formato inesperado.")
        if not _is_https_url(profile_url):
            continue
        findings.append(
            Finding(
                category="Perfil público — username candidato",
                source=f"Maigret — {site}",
                title=profile_url.rstrip("/").rsplit("/", 1)[-1],
                url=profile_url,
                observed_at="",
                details=(
                    "Maigret reportou um perfil para um username inferido a partir "
                    "do nome da empresa. Isso não confirma vínculo ou oficialidade."
                ),
            )
        )
    return findings


def search_maigret_usernames(usernames: list[str]) -> SourceResult:
    if not SHERLOCK_PYTHON.is_file():
        return SourceResult(
            name="Perfis — Maigret",
            findings=[],
            error=(
                "Maigret não está instalado neste ambiente. No Windows, execute "
                "setup_sherlock.ps1; no Streamlit Cloud, confira as dependências "
                "do requirements.txt e os logs de implantação."
            ),
        )
    if not usernames:
        return SourceResult(name="Perfis — Maigret", findings=[])
    if len(usernames) > MAX_SHERLOCK_USERNAMES or any(
        not re.fullmatch(r"[A-Za-z0-9._-]{2,30}", username)
        for username in usernames
    ):
        return SourceResult(
            name="Perfis — Maigret",
            findings=[],
            error="A lista de usernames candidatos é inválida.",
        )

    try:
        completed = subprocess.run(
            [
                str(SHERLOCK_PYTHON),
                "-m",
                "maigret_runner",
                *usernames,
            ],
            capture_output=True,
            text=True,
            timeout=MAIGRET_TIMEOUT,
            check=False,
            cwd=Path(__file__).resolve().parent,
        )
    except subprocess.TimeoutExpired:
        return SourceResult(
            name="Perfis — Maigret",
            findings=[],
            error=f"A consulta do Maigret excedeu {MAIGRET_TIMEOUT} segundos.",
        )
    except OSError as error:
        return SourceResult(name="Perfis — Maigret", findings=[], error=str(error))

    try:
        findings = _parse_maigret_output(completed.stdout)
    except (json.JSONDecodeError, ValueError) as error:
        return SourceResult(
            name="Perfis — Maigret",
            findings=[],
            error=f"Não foi possível interpretar a resposta do Maigret: {error}",
        )

    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip()
        return SourceResult(
            name="Perfis — Maigret",
            findings=findings,
            error=error[:500] or f"Maigret terminou com código {completed.returncode}.",
        )
    return SourceResult(name="Perfis — Maigret", findings=findings)


def consolidate_findings(results: list[SourceResult]) -> list[Finding]:
    findings: list[Finding] = []
    indexes_by_url: dict[str, int] = {}
    for result in results:
        for finding in result.findings:
            existing_index = indexes_by_url.get(finding.url)
            if existing_index is None:
                indexes_by_url[finding.url] = len(findings)
                findings.append(finding)
                continue

            existing = findings[existing_index]
            sources = dict.fromkeys(
                [*existing.source.split(" · "), *finding.source.split(" · ")]
            )
            findings[existing_index] = Finding(
                category=existing.category,
                source=" · ".join(sources),
                title=existing.title,
                url=existing.url,
                observed_at=existing.observed_at or finding.observed_at,
                details=existing.details,
            )
    return findings
