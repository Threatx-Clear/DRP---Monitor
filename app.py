import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape
from io import StringIO

import pandas as pd
import streamlit as st

import sources
from sources import (
    Finding,
    SourceResult,
    infer_brand_terms,
    infer_domain_candidates,
    infer_typosquat_candidates,
    infer_usernames,
)


st.set_page_config(
    page_title="DRP — Monitor de risco digital",
    page_icon=":material/security:",
    layout="wide",
)

RESULT_COLUMNS = ["Tipo", "Resultado", "Fonte", "Evidência", "Data", "Link"]
REPORT_COLUMNS = [*RESULT_COLUMNS, "Triagem", "Observação"]
RESULT_FILTERS = ("Tudo", "Perfis", "Sites e domínios")
REVIEW_STATUSES = ("Pendente", "Relevante", "Falso positivo", "Validado")

if "result_filter" not in st.session_state:
    st.session_state["result_filter"] = "Tudo"


def build_report(
    findings: list[Finding],
    brand: str,
    generated_at: str,
    shortcuts: list[Finding] | None = None,
    reviews: dict[str, dict[str, str]] | None = None,
) -> str:
    rows = []
    for finding in findings:
        review = (reviews or {}).get(finding_review_key(finding), {})
        rows.append(
            "<tr>"
            f"<td>{escape(finding.category)}</td>"
            f"<td>{escape(finding.title)}</td>"
            f"<td>{escape(finding.source)}</td>"
            f"<td>{escape(finding.details)}</td>"
            f"<td>{escape(finding.observed_at or 'Não informado')}</td>"
            f'<td><a href="{escape(finding.url, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">Abrir fonte</a></td>'
            f"<td>{escape(review.get('Triagem', REVIEW_STATUSES[0]))}</td>"
            f"<td>{escape(review.get('Observação', ''))}</td>"
            "</tr>"
        )

    table_rows = "\n".join(rows) or (
        f'<tr><td colspan="{len(REPORT_COLUMNS)}">Nenhum resultado nesta coleta.</td></tr>'
    )
    headers = "".join(f"<th>{escape(column)}</th>" for column in REPORT_COLUMNS)
    shortcut_links = "\n".join(
        f'<li><a href="{escape(finding.url, quote=True)}" '
        f'target="_blank" rel="noopener noreferrer">{escape(finding.title)}</a> '
        f"— {escape(finding.source)}</li>"
        for finding in shortcuts or []
    )
    shortcut_section = (
        "<h2>Pesquisas manuais</h2>"
        "<p>Estes links abrem pesquisas externas; os resultados não foram coletados.</p>"
        f"<ul>{shortcut_links}</ul>"
        if shortcut_links
        else ""
    )
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <title>Relatório DRP: {escape(brand)}</title>
  <style>
    body {{ font: 15px Arial, sans-serif; margin: 2rem auto; max-width: 1200px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
    th {{ background: #f2f2f2; }}
    td {{ overflow-wrap: anywhere; }}
  </style>
</head>
<body>
  <h1>Relatório de Digital Risk Protection: {escape(brand)}</h1>
  <p>Gerado em {escape(generated_at)}. Resultados: {len(findings)}.</p>
  <p>Os nomes de usuário e domínios consultados foram inferidos do nome informado.
  Resultados são candidatos para triagem, não prova de vínculo, oficialidade ou fraude.</p>
  <table><thead><tr>{headers}</tr></thead><tbody>{table_rows}</tbody></table>
  {shortcut_section}
</body>
</html>"""


def build_csv(
    findings: list[Finding],
    reviews: dict[str, dict[str, str]] | None = None,
) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(REPORT_COLUMNS)
    for finding in findings:
        review = (reviews or {}).get(finding_review_key(finding), {})
        writer.writerow(
            [
                finding.category,
                finding.title,
                finding.source,
                finding.details,
                finding.observed_at,
                finding.url,
                review.get("Triagem", REVIEW_STATUSES[0]),
                review.get("Observação", ""),
            ]
        )
    return output.getvalue().encode("utf-8-sig")


def finding_review_key(finding: Finding) -> str:
    return f"{finding.source}|{finding.url.rstrip('/').casefold()}"


def build_findings_dataframe(
    findings: list[Finding],
    reviews: dict[str, dict[str, str]],
    selected_findings: list[str] | None = None,
) -> pd.DataFrame:
    selected_ids = set(selected_findings or [])
    rows = []
    for finding in findings:
        review = reviews.get(finding_review_key(finding), {})
        rows.append(
            {
                "Selecionar": finding_review_key(finding) in selected_ids,
                "ID": finding_review_key(finding),
                "Tipo": finding.category,
                "Resultado": finding.title,
                "Fonte": finding.source,
                "Evidência": finding.details,
                "Data": finding.observed_at,
                "Link": finding.url,
                "Triagem": review.get("Triagem", REVIEW_STATUSES[0]),
                "Observação": review.get("Observação", ""),
            }
        )
    return pd.DataFrame(rows, columns=["Selecionar", "ID", *REPORT_COLUMNS])


def consolidate_findings(results: list[SourceResult]) -> list[Finding]:
    findings = []
    seen = set()
    for source in results:
        for finding in source.findings:
            if finding.source.startswith("URLScan"):
                key = (finding.source, finding.url.rstrip("/").casefold())
            else:
                key = (finding.source, finding.url.rstrip("/").casefold())
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
    return findings


def reset_result_filter() -> None:
    st.session_state["result_filter"] = "Tudo"
    reset_findings_editor()


def reset_findings_editor() -> None:
    for filter_name in RESULT_FILTERS:
        st.session_state.pop(f"findings_editor_{filter_name}", None)


st.title(":material/shield: Monitor de exposição digital")
st.caption(
    "Mapeie sinais públicos relacionados a uma marca em uma única pesquisa."
)

with st.container(border=True):
    st.subheader("Comece pelo nome da marca")
    st.caption(
        "Os termos, perfis candidatos e possíveis typosquats são inferidos automaticamente."
    )
    with st.form("research_form"):
        search_column, button_column = st.columns([4, 1], vertical_alignment="bottom")
        brand = search_column.text_input(
            "Nome da empresa",
            placeholder="Ex.: Wepink",
            help="Informe somente o nome. As variações serão geradas durante a pesquisa.",
        )
        submitted = button_column.form_submit_button(
            "Pesquisar",
            type="primary",
            icon=":material/search:",
            width="stretch",
            on_click=reset_result_filter,
        )

if submitted:
    st.session_state.pop("research", None)
    try:
        terms = infer_brand_terms(brand)
        usernames = infer_usernames(brand)
        domains = infer_domain_candidates(brand)
        typosquats = infer_typosquat_candidates(brand)
        if not usernames or not domains:
            raise ValueError("Não foi possível gerar candidatos a partir desse nome.")
    except ValueError as error:
        st.error(str(error))
    else:
        with st.spinner("Pesquisando fontes públicas. Isso pode levar alguns instantes..."):
            with ThreadPoolExecutor(max_workers=4) as executor:
                certificate_future = executor.submit(sources.search_certificates, domains)
                site_future = executor.submit(sources.search_public_sites, terms, domains)
                typosquat_future = executor.submit(
                    sources.search_urlscan_domains, domains, typosquats
                )
                profile_future = executor.submit(
                    sources.search_sherlock_usernames, usernames
                )
                results = [
                    *certificate_future.result(),
                    typosquat_future.result(),
                    *site_future.result(),
                    profile_future.result(),
                ]
        shortcuts = [
            *sources.build_document_search_findings(brand),
            *sources.build_ad_library_findings(brand),
        ]
        st.session_state["research"] = {
            "brand": " ".join(brand.split()),
            "terms": terms,
            "usernames": usernames,
            "domains": domains,
            "typosquats": typosquats,
            "results": results,
            "shortcuts": shortcuts,
            "reviews": {},
            "selected_findings": [],
            "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        }

research = st.session_state.get("research")
if research and research.pop("reset_findings_editor", False):
    reset_findings_editor()

if research:
    review_notice = research.pop("review_notice", None)
    if review_notice:
        st.success(review_notice)

    st.divider()
    result_heading = st.container()
    result_heading.subheader(f"Exposição encontrada: {research['brand']}")
    result_heading.caption(
        f"Atualizado em {research['generated_at']} · "
        f"{len(research['terms'])} termos pesquisados"
    )

    results: list[SourceResult] = research["results"]
    findings = consolidate_findings(results)
    profile_count = sum(
        finding.category == "Perfil público — username candidato"
        for finding in findings
    )
    domain_count = sum(
        finding.category == "Certificado — domínio candidato"
        for finding in findings
    )
    other_count = len(findings) - profile_count - domain_count

    rate_limited_sources = [
        source.name
        for source in results
        if source.error and "HTTP 429" in source.error
    ]
    if rate_limited_sources:
        st.warning(
            "Resultados incompletos: algumas fontes limitaram as consultas. "
            "Aguarde antes de pesquisar novamente. Confira “Fontes e status da coleta” "
            "para ver quais fontes foram afetadas.",
            icon=":material/hourglass_top:",
        )

    metrics = st.columns(3)
    metric_data = (
        ("Itens encontrados", len(findings), ":material/travel_explore:"),
        ("Perfis candidatos", profile_count, ":material/person_search:"),
        ("Sites e domínios", other_count + domain_count, ":material/language:"),
    )
    for column, (label, value, icon) in zip(metrics, metric_data):
        with column, st.container(border=True):
            st.metric(label, value, icon=icon)

    shortcuts: list[Finding] = research["shortcuts"]
    document_shortcuts = [
        finding
        for finding in shortcuts
        if finding.category == "Documentos — pesquisa manual"
    ]
    ad_shortcuts = [
        finding
        for finding in shortcuts
        if finding.category == "Anúncios — pesquisa na biblioteca"
    ]
    with st.container(border=True):
        st.subheader("Pesquisas manuais")
        st.caption(
            "Estes atalhos abrem buscas externas. Seus resultados não fazem parte "
            "da lista de achados nem são coletados automaticamente."
        )
        document_column, ad_column = st.columns(2)
        with document_column:
            st.markdown("**Documentos e PDFs**")
            for finding in document_shortcuts:
                st.link_button(finding.title, finding.url, width="stretch")
        with ad_column:
            st.markdown("**Bibliotecas de anúncios**")
            for finding in ad_shortcuts:
                st.link_button(finding.title, finding.url, width="stretch")

    filter_column, count_column = st.columns([3, 1], vertical_alignment="bottom")
    selected_filter = filter_column.segmented_control(
        "Filtrar resultados",
        options=RESULT_FILTERS,
        key="result_filter",
        label_visibility="collapsed",
        width="stretch",
        on_change=reset_findings_editor,
    )
    if selected_filter == "Perfis":
        visible_findings = [
            finding
            for finding in findings
            if finding.category == "Perfil público — username candidato"
        ]
    elif selected_filter == "Sites e domínios":
        visible_findings = [
            finding
            for finding in findings
            if finding.category != "Perfil público — username candidato"
        ]
    else:
        visible_findings = findings

    count_column.caption(
        f"Exibindo {len(visible_findings)} de {len(findings)} itens"
    )

    if visible_findings:
        edited_findings = st.data_editor(
            build_findings_dataframe(
                visible_findings,
                research["reviews"],
                research.get("selected_findings", []),
            ),
            hide_index=True,
            width="stretch",
            height=min(600, 42 * (len(visible_findings) + 1)),
            key=f"findings_editor_{selected_filter}",
            disabled=["ID", *RESULT_COLUMNS],
            column_config={
                "Selecionar": st.column_config.CheckboxColumn(
                    "Selecionar",
                    help="Marque os registros que receberão a classificação em lote.",
                    width="small",
                ),
                "ID": None,
                "Tipo": st.column_config.TextColumn("Tipo", width="medium"),
                "Resultado": st.column_config.TextColumn("Resultado", width="medium"),
                "Fonte": st.column_config.TextColumn("Fonte", width="medium"),
                "Evidência": st.column_config.TextColumn("Evidência", width="large"),
                "Data": st.column_config.TextColumn("Data", width="small"),
                "Link": st.column_config.LinkColumn("Abrir fonte", width="small"),
                "Triagem": st.column_config.SelectboxColumn(
                    "Triagem",
                    options=REVIEW_STATUSES,
                    required=True,
                    width="medium",
                ),
                "Observação": st.column_config.TextColumn(
                    "Observação",
                    width="large",
                ),
            },
        )
        selected_ids = set(research.get("selected_findings", []))
        visible_ids = {
            finding_review_key(finding) for finding in visible_findings
        }
        selected_ids.difference_update(visible_ids)
        for row in edited_findings.to_dict(orient="records"):
            research["reviews"][row["ID"]] = {
                "Triagem": row["Triagem"],
                "Observação": row["Observação"],
            }
            if row["Selecionar"]:
                selected_ids.add(row["ID"])
        research["selected_findings"] = sorted(selected_ids)
    else:
        st.info(
            "Nenhum item nesta categoria.",
            icon=":material/filter_alt_off:",
        )

    selected_count = len(research.get("selected_findings", []))
    with st.container(border=True):
        st.subheader("Triagem em lote")
        st.caption(
            f"{selected_count} registro(s) selecionado(s). "
            "A observação compartilhada é opcional; se ficar vazia, "
            "as observações individuais serão mantidas."
        )
        with st.form("bulk_review_form"):
            bulk_status_column, bulk_note_column, bulk_action_column = st.columns(
                [2, 4, 1],
                vertical_alignment="bottom",
            )
            bulk_status = bulk_status_column.selectbox(
                "Classificação para os selecionados",
                options=REVIEW_STATUSES,
                index=1,
            )
            bulk_observation = bulk_note_column.text_input(
                "Observação compartilhada",
                placeholder="Opcional — mantém as observações atuais se ficar vazia",
            )
            apply_bulk_review = bulk_action_column.form_submit_button(
                "Aplicar",
                type="primary",
                icon=":material/check:",
                width="stretch",
                disabled=selected_count == 0,
            )
        if apply_bulk_review:
            selected_ids = set(research.get("selected_findings", []))
            for finding_id in selected_ids:
                review = research["reviews"].setdefault(
                    finding_id,
                    {"Triagem": REVIEW_STATUSES[0], "Observação": ""},
                )
                review["Triagem"] = bulk_status
                if bulk_observation.strip():
                    review["Observação"] = bulk_observation.strip()
            research["selected_findings"] = []
            research["reset_findings_editor"] = True
            research["review_notice"] = (
                f"Classificação aplicada a {len(selected_ids)} registro(s)."
            )
            st.rerun()

    with st.expander("Como os candidatos foram inferidos"):
        st.write("**Termos:**", ", ".join(research["terms"]))
        st.write("**Usernames testados:**", ", ".join(research["usernames"]))
        st.write(
            "**Domínios consultados (não confirmados como oficiais):**",
            ", ".join(research["domains"]),
        )
        st.write(
            f"**Variações de typosquatting geradas e consultadas "
            f"({len(research['typosquats'])}):**",
            ", ".join(research["typosquats"]) or "Nenhuma variação gerada.",
        )

    with st.expander("Fontes e status da coleta"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Fonte": source.name,
                        "Status": (
                            "Atenção"
                            if source.error
                            else "Sem resultados"
                            if not source.findings
                            else "Concluída"
                        ),
                        "Resultados": len(source.findings),
                        "Detalhe": source.error
                        or (
                            "Consulta concluída; nenhum resultado encontrado."
                            if not source.findings
                            else "Consulta concluída."
                        ),
                    }
                    for source in results
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Fonte": st.column_config.TextColumn("Fonte", width="medium"),
                "Status": st.column_config.TextColumn("Status", width="small"),
                "Resultados": st.column_config.NumberColumn(
                    "Resultados", width="small"
                ),
                "Detalhe": st.column_config.TextColumn("Detalhe", width="large"),
            },
        )

    st.warning(
        "Resultados são pistas de exposição: perfis e domínios candidatos não confirmam vínculo ou fraude.",
        icon=":material/info:",
    )
    with st.expander("Cobertura e limitações"):
        st.write(
            "O Sherlock verifica usernames inferidos em Instagram, LinkedIn, TikTok, "
            "X/Twitter, YouTube, Telegram e Mercado Livre. Facebook, OLX e Shopee "
            "não estão cobertos. Os atalhos de documentos e anúncios ficam em uma "
            "seção separada dos achados; buscas externas não são importadas automaticamente."
        )
        st.caption(
            "O URLScan pode retornar até 100 registros por termo. A quantidade real "
            "depende do acervo disponível e das cotas de consulta."
        )

    with st.container(horizontal=True, horizontal_alignment="right"):
        st.download_button(
            "Baixar CSV",
            data=build_csv(findings, research["reviews"]),
            file_name="resultados_drp.csv",
            mime="text/csv",
            icon=":material/download:",
        )
        st.download_button(
            "Baixar relatório HTML",
            data=build_report(
                findings,
                research["brand"],
                research["generated_at"],
                shortcuts,
                research["reviews"],
            ),
            file_name="relatorio_drp.html",
            mime="text/html",
            icon=":material/description:",
        )
else:
    with st.container(border=True):
        st.subheader("Uma visão ampla da presença pública")
        st.write(
            "A partir de um nome, o monitor reúne indicadores passivos de perfis, "
            "domínios, certificados, sites e atalhos para pesquisas de anúncios e documentos."
        )
        overview = st.columns(3)
        overview[0].markdown(
            "**:material/person_search: Perfis**  \n"
            "Usernames candidatos verificados em redes públicas."
        )
        overview[1].markdown(
            "**:material/language: Sites e domínios**  \n"
            "Certificados, registros do URLScan e variações de typosquatting."
        )
        overview[2].markdown(
            "**:material/description: Anúncios e documentos**  \n"
            "Atalhos para pesquisas nas bibliotecas de anúncios, PDFs e Scribd."
        )
    st.caption(
        "Fontes públicas e gratuitas; os resultados podem ser incompletos e exigem revisão."
    )
