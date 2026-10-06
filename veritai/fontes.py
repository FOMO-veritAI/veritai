"""Descoberta e leitura de fontes públicas.

A busca na web serve só para descobrir documentos; o resultado é decidido
pela comparação entre a afirmação e os trechos lidos.
"""

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from .config import GOOGLE_FACT_CHECK_API_KEY


USER_AGENT = "VeritAI/0.1 (local research prototype)"
MAX_BYTES = 1_500_000


@dataclass
class Fonte:
    url: str
    titulo: str
    dominio: str
    texto: str
    origem: str  # busca_web | checagem_externa | link_publisher | anexo
    data_publicacao: str | None = None


def safe_public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, None)
        return bool(addresses) and all(ipaddress.ip_address(entry[4][0]).is_global for entry in addresses)
    except (socket.gaierror, ValueError):
        return False


async def ler_pagina(url: str, origem: str = "busca_web") -> Fonte:
    if not await asyncio.to_thread(safe_public_url, url):
        raise ValueError("URL não é pública ou não pôde ser resolvida.")
    async with httpx.AsyncClient(timeout=12, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(url)
        if response.is_redirect:
            raise ValueError("Redirecionamentos não são aceitos para fontes fornecidas.")
        response.raise_for_status()
        if len(response.content) > MAX_BYTES:
            raise ValueError("A fonte excede o tamanho permitido.")
        if "text/html" not in response.headers.get("content-type", ""):
            raise ValueError("A fonte precisa ser uma página HTML pública.")
    soup = BeautifulSoup(response.text, "html.parser")
    titulo = soup.title.get_text(" ", strip=True) if soup.title else urlparse(url).hostname or url
    data = None
    meta = soup.find("meta", attrs={"property": "article:published_time"})
    if meta and meta.get("content"):
        data = str(meta["content"])[:10]
    for element in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        element.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    texto = re.sub(r"\s+", " ", main.get_text(" ", strip=True))[:40000]
    if len(texto) < 80:
        raise ValueError("A página não contém texto suficiente para análise.")
    return Fonte(url=url, titulo=titulo[:300], dominio=urlparse(url).hostname or "", texto=texto, origem=origem, data_publicacao=data)


def termos_de_busca(afirmacao: str) -> str:
    words = re.findall(r"[\wÀ-ÿ-]+", afirmacao, re.UNICODE)
    stops = {"de", "da", "do", "dos", "das", "em", "com", "para", "por", "uma", "um", "que", "foi", "será", "vai", "entre", "sobre", "ao", "aos", "as", "os", "se"}
    useful = [word for word in words if len(word) > 2 and word.lower() not in stops]
    return " ".join(useful[:8]) or afirmacao[:90]


async def gdelt_urls(afirmacao: str) -> list[str]:
    params = {"query": termos_de_busca(afirmacao), "mode": "artlist", "format": "json", "maxrecords": "8", "timespan": "3months"}
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params)
        response.raise_for_status()
        data = response.json()
    return [item["url"] for item in data.get("articles", []) if item.get("url")]


async def bing_news_urls(afirmacao: str) -> list[str]:
    async with httpx.AsyncClient(timeout=12, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get("https://www.bing.com/news/search", params={"q": termos_de_busca(afirmacao), "format": "rss", "mkt": "pt-BR"})
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
    urls = []
    for item in root.findall("./channel/item")[:8]:
        link = item.findtext("link") or ""
        # O feed usa um link de redirecionamento; a URL da fonte está no parâmetro url.
        actual = parse_qs(urlparse(link).query).get("url", [link])[0]
        if actual.startswith("https://") or actual.startswith("http://"):
            urls.append(actual)
    return urls


async def google_fact_check_urls(afirmacao: str) -> list[str]:
    if not GOOGLE_FACT_CHECK_API_KEY:
        return []
    params = {"query": termos_de_busca(afirmacao), "languageCode": "pt", "pageSize": 5, "key": GOOGLE_FACT_CHECK_API_KEY}
    async with httpx.AsyncClient(timeout=12, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get("https://factchecktools.googleapis.com/v1alpha1/claims:search", params=params)
        response.raise_for_status()
        data = response.json()
    return [review["url"] for claim in data.get("claims", []) for review in claim.get("claimReview", []) if review.get("url")]


async def buscar_fontes(afirmacao: str) -> tuple[list[Fonte], list[str]]:
    urls: list[tuple[str, str]] = []
    erros: list[str] = []
    for rotulo, origem, finder in (
        ("Bing News", "busca_web", bing_news_urls),
        ("GDELT", "busca_web", gdelt_urls),
        ("Google Fact Check", "checagem_externa", google_fact_check_urls),
    ):
        try:
            urls.extend((url, origem) for url in await finder(afirmacao))
        except Exception as exc:
            erros.append(f"{rotulo}: {type(exc).__name__}")
    unicos = list(dict.fromkeys(urls))[:10]
    paginas = await asyncio.gather(*(ler_pagina(url, origem) for url, origem in unicos), return_exceptions=True)
    fontes = [pagina for pagina in paginas if isinstance(pagina, Fonte)]
    if unicos and not fontes:
        erros.append("Os resultados foram encontrados, mas as páginas não puderam ser lidas.")
    return fontes, erros
