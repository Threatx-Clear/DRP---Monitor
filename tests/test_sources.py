import unittest
from unittest.mock import Mock, patch

import requests

from sources import (
    _parse_sherlock_output,
    build_ad_library_findings,
    build_document_search_findings,
    infer_brand_terms,
    infer_domain_candidates,
    infer_typosquat_candidates,
    infer_usernames,
    normalize_domain,
    search_certificates,
    search_public_sites,
    search_sherlock_usernames,
    search_urlscan_domains,
    validate_scope,
)


class InferenceTests(unittest.TestCase):
    def test_infers_terms_usernames_and_domains_from_one_company_name(self):
        brand = "O Boticário"

        self.assertEqual(
            infer_brand_terms(brand),
            ["O Boticário", "o boticario", "boticario", "oboticario"],
        )
        self.assertEqual(
            infer_usernames(brand),
            ["boticario", "oboticario", "boticariooficial", "oficialboticario"],
        )
        self.assertEqual(
            infer_domain_candidates(brand),
            ["boticario.com.br", "boticario.com"],
        )

    def test_deduplicates_search_terms_without_case_sensitivity(self):
        self.assertEqual(infer_brand_terms("Wepink"), ["Wepink"])

    def test_infers_bounded_typo_candidates(self):
        candidates = infer_typosquat_candidates("Marca")

        self.assertGreater(len(candidates), 12)
        self.assertLessEqual(len(candidates), 192)
        self.assertIn("mrca.com.br", candidates)
        self.assertIn("mraca.com.br", candidates)
        self.assertIn("m4rca.com.br", candidates)

    def test_doubles_typosquat_domains_for_wepink(self):
        candidates = infer_typosquat_candidates("Wepink")

        self.assertEqual(len(candidates), 192)
        self.assertEqual(len(set(candidates)), 192)

    def test_builds_ad_library_search_links_for_the_brand(self):
        findings = build_ad_library_findings("O Boticário")

        self.assertEqual(len(findings), 2)
        self.assertEqual(
            {finding.source for finding in findings},
            {"Meta Ad Library", "Google Ads Transparency Center"},
        )
        self.assertTrue(all(finding.category.startswith("Anúncios") for finding in findings))
        self.assertTrue(all("O+Botic%C3%A1rio" in finding.url for finding in findings))
        self.assertTrue(all("não são importados automaticamente" in finding.details for finding in findings))

    def test_builds_manual_document_search_links(self):
        findings = build_document_search_findings("O Boticário")

        self.assertEqual(len(findings), 3)
        self.assertEqual(
            {finding.source for finding in findings},
            {"Google — PDFs", "Bing — PDFs", "Scribd"},
        )
        self.assertTrue(
            all(finding.category == "Documentos — pesquisa manual" for finding in findings)
        )
        self.assertIn("filetype%3Apdf", findings[0].url)
        self.assertIn("filetype%3Apdf", findings[1].url)
        self.assertIn("O+Botic%C3%A1rio", findings[2].url)
        self.assertTrue(
            all("não são coletados" in finding.details for finding in findings)
        )

    def test_rejects_names_without_searchable_letters_or_digits(self):
        with self.assertRaisesRegex(ValueError, "letras ou números"):
            infer_brand_terms(" !!! ")

    def test_normalizes_domain_and_validates_inferred_scope(self):
        self.assertEqual(normalize_domain("WWW.Example.com."), "www.example.com")
        terms, domains = validate_scope(
            infer_brand_terms("Marca"),
            infer_domain_candidates("Marca"),
        )
        self.assertEqual(terms[0], "Marca")
        self.assertEqual(domains, ["marca.com.br", "marca.com"])

    def test_domain_validation_rejects_urls_and_ip_addresses(self):
        with self.assertRaisesRegex(ValueError, "sem protocolo"):
            normalize_domain("https://example.com")
        with self.assertRaisesRegex(ValueError, "endereço IP"):
            normalize_domain("192.0.2.1")


class PassiveSourceTests(unittest.TestCase):
    @patch("sources.requests.get")
    def test_certificates_return_candidate_subdomains(self, get):
        response = Mock()
        response.json.return_value = [
            {
                "dns_names": ["*.marca.com.br", "marca.com.br", "other.test"],
                "not_before": "2026-01-01",
                "not_after": "2027-01-01",
            }
        ]
        get.return_value = response

        result = search_certificates(["marca.com.br"])

        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].error)
        self.assertEqual(
            [finding.title for finding in result[0].findings],
            ["marca.com.br"],
        )
        self.assertIn("não foi confirmado", result[0].findings[0].details)

    @patch("sources.requests.get")
    def test_urlscan_returns_public_records_and_deduplicates(self, get):
        response = Mock()
        response.json.return_value = {
            "results": [
                {
                    "task": {
                        "domain": "lookalike.test",
                        "uuid": "11234567-89ab-cdef-0123-456789abcdef",
                        "time": "2026-10-31T12:00:00Z",
                    },
                    "page": {
                        "domain": "lookalike.test",
                        "title": "Marca login",
                    },
                }
            ]
        }
        get.return_value = response

        results = search_public_sites(["Marca", "marca"], ["marca.com.br"])

        self.assertEqual(len(results), 2)
        self.assertIsNone(results[0].error)
        self.assertEqual(len(results[0].findings), 1)
        self.assertEqual(len(results[1].findings), 1)
        self.assertNotEqual(
            results[0].findings[0].source,
            results[1].findings[0].source,
        )
        self.assertIn("urlscan.io/result/11234567", results[0].findings[0].url)
        self.assertEqual(get.call_args_list[0].kwargs["params"]["size"], 100)

    @patch("sources.requests.get")
    def test_urlscan_returns_up_to_100_records_per_term(self, get):
        response = Mock()
        response.json.return_value = {
            "results": [
                {
                    "task": {
                        "domain": f"result{index}.test",
                        "uuid": f"{index:032x}",
                    },
                    "page": {
                        "domain": f"result{index}.test",
                        "title": f"Marca {index}",
                    },
                }
                for index in range(100)
            ]
        }
        get.return_value = response

        results = search_public_sites(["Marca", "Marca oficial"], [])

        self.assertEqual([len(result.findings) for result in results], [100, 100])
        self.assertEqual(sum(len(result.findings) for result in results), 200)
        self.assertTrue(all(result.name.startswith("URLScan — ") for result in results))

    @patch("sources.requests.get", side_effect=requests.Timeout("tempo excedido"))
    def test_source_error_is_reported(self, get):
        results = search_public_sites(["Marca"], ["marca.com.br"])

        self.assertEqual(results[0].findings, [])
        self.assertIn("tempo excedido", results[0].error)
        get.assert_called_once()

    @patch("sources.requests.get")
    def test_urlscan_rate_limit_error_includes_retry_guidance(self, get):
        response = Mock(status_code=429, headers={"Retry-After": "60"})
        get.return_value = response

        result = search_public_sites(["Marca"], [])[0]

        self.assertEqual(result.findings, [])
        self.assertIn("HTTP 429", result.error)
        self.assertIn("Tente novamente após 60", result.error)
        response.raise_for_status.assert_not_called()

    @patch("sources.requests.get")
    def test_invalid_urlscan_response_is_reported(self, get):
        response = Mock()
        response.json.return_value = {"results": None}
        get.return_value = response

        result = search_public_sites(["Marca"], ["marca.com.br"])[0]

        self.assertEqual(result.findings, [])
        self.assertIn("não contém resultados válidos", result.error)

    @patch("sources.requests.get")
    def test_urlscan_searches_inferred_typosquat_domains(self, get):
        response = Mock()
        response.json.return_value = {
            "results": [
                {
                    "task": {
                        "domain": "mraca.com.br",
                        "uuid": "21234567-89ab-cdef-0123-456789abcdef",
                        "time": "2026-10-31T12:00:00Z",
                    },
                    "page": {"domain": "mraca.com.br", "title": "Marca"},
                },
                {
                    "task": {
                        "domain": "unrelated.test",
                        "uuid": "31234567-89ab-cdef-0123-456789abcdef",
                    },
                    "page": {"domain": "unrelated.test"},
                },
            ]
        }
        get.return_value = response

        result = search_urlscan_domains(
            ["marca.com.br"],
            ["mraca.com.br"],
        )

        self.assertIsNone(result.error)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(
            result.findings[0].category,
            "Possível typosquat — revisar",
        )
        self.assertEqual(result.findings[0].title, "mraca.com.br")

    @patch("sources.requests.get")
    def test_urlscan_batches_large_typosquat_lists(self, get):
        response = Mock()
        response.json.return_value = {"results": []}
        get.return_value = response
        typo_domains = [f"marca{index}.com" for index in range(100)]

        result = search_urlscan_domains(["marca.com"], typo_domains)

        self.assertIsNone(result.error)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(
            sum(
                call.kwargs["params"]["q"].count("page.domain:")
                for call in get.call_args_list
            ),
            101,
        )

    @patch("sources.requests.get")
    def test_expanded_typosquat_list_is_split_into_safe_urlscan_batches(self, get):
        response = Mock()
        response.json.return_value = {"results": []}
        get.return_value = response
        typo_domains = infer_typosquat_candidates("Wepink")

        result = search_urlscan_domains(
            infer_domain_candidates("Wepink"),
            typo_domains,
        )

        self.assertIsNone(result.error)
        self.assertEqual(get.call_count, 5)
        self.assertTrue(
            all(
                1 <= call.kwargs["params"]["q"].count("page.domain:") <= 48
                for call in get.call_args_list
            )
        )


class SherlockTests(unittest.TestCase):
    def test_parses_only_found_https_profile_lines(self):
        findings = _parse_sherlock_output(
            "[+] Instagram: https://instagram.com/marca/\n"
            "[+] LinkedIn: http://linkedin.com/in/marca\n"
            "[-] TikTok: Not Found\n"
            "[+] Invalid: javascript:alert(1)\n"
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].source, "Sherlock — Instagram")
        self.assertEqual(findings[0].title, "marca")

    @patch("sources.SHERLOCK_PYTHON")
    def test_reports_when_sherlock_is_not_installed(self, python):
        python.is_file.return_value = False

        result = search_sherlock_usernames(["marca"])

        self.assertEqual(result.findings, [])
        self.assertIn("setup_sherlock.ps1", result.error)

    @patch("sources.subprocess.run")
    @patch("sources.SHERLOCK_PYTHON")
    def test_invokes_sherlock_module_with_limited_public_sites(self, python, run):
        python.is_file.return_value = True
        run.return_value = Mock(
            returncode=0,
            stdout="[+] Instagram: https://instagram.com/marca/\n",
            stderr="",
        )

        result = search_sherlock_usernames(["marca", "marcaoficial"])

        self.assertEqual(len(result.findings), 1)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], [str(python), "-m", "sherlock_project"])
        self.assertIn("--site", command)
        self.assertIn("Instagram", command)
        self.assertIn("mercadolivre", command)
        self.assertIn("--local", command)
        self.assertNotIn("Facebook", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 120)


if __name__ == "__main__":
    unittest.main()
