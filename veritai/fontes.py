"""Descoberta e leitura de fontes públicas.

A busca na web serve só para descobrir documentos; o resultado é decidido
pela comparação entre a afirmação e os trechos lidos.
"""

import asyncio
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from .config import GOOGLE_FACT_CHECK_API_KEY
from .relatorio import ChecagemAnterior


USER_AGENT = "VeritAI/0.1 (local research prototype)"
# Sem compressão, cada bloco lido da rede tem o tamanho que chegou, sem expansão na descompressão.
CABECALHOS = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
MAX_BYTES = 1_500_000


@dataclass
class Fonte:
    url: str
    titulo: str
    dominio: str
    texto: str
    origem: str  # busca_web | link_publisher | anexo | base_propria
    data_publicacao: str | None = None
    texto_completo: str = ""  # documento inteiro, quando texto é só um trecho (base própria)
    copia_de: str = ""  # grupo de cópias já gravado na base própria
    grupo: str = ""  # fonte independente: mesmo domínio ou cópia quase literal


def dominio_da_fonte(url: str, nome: str) -> str:
    # Documentos sem URL viram uma fonte própria, identificada pelo nome.
    return urlparse(url).hostname or f"documento:{nome}"


@dataclass(frozen=True)
class Destino:
    """Endereço já validado de uma URL: a conexão vai para o IP, e Host e TLS usam o nome original."""

    host: str  # nome do host em ASCII (IDNA), usado no SNI e na verificação do certificado
    ip: str
    url_conexao: str  # URL com o IP no lugar do nome
    cabecalho_host: str


def resolver_dns(host: str) -> list[str]:
    return [entrada[4][0] for entrada in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]


def endereco_publico(endereco: str) -> bool:
    ip = ipaddress.ip_address(endereco.split("%")[0])
    # IPv4 escrito como IPv6 (::ffff:10.0.0.1) vale pelo endereço IPv4.
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def resolver_publico(url: str) -> Destino:
    """Resolve o host uma única vez e exige que todos os endereços sejam públicos.

    A conexão usa o IP devolvido aqui, para que o domínio não possa trocar de endereço
    entre a checagem e a conexão.
    """
    recusada = ValueError("URL não é pública ou não pôde ser resolvida.")
    try:
        parsed = urlparse(url)
        porta = parsed.port
        host = (parsed.hostname or "").encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        raise recusada from None
    if parsed.scheme not in {"https", "http"} or not host or parsed.username or parsed.password or porta not in {None, 80, 443}:
        raise recusada
    try:
        enderecos = list(dict.fromkeys(resolver_dns(host)))
        if not enderecos or not all(endereco_publico(endereco) for endereco in enderecos):
            raise recusada
    except (OSError, ValueError, UnicodeError):
        raise recusada from None
    ip = enderecos[0].split("%")[0]
    sufixo_porta = f":{porta}" if porta else ""
    return Destino(
        host=host,
        ip=ip,
        url_conexao=parsed._replace(netloc=entre_colchetes(ip) + sufixo_porta).geturl(),
        # Uma URL com IPv6 literal também precisa de colchetes no Host.
        cabecalho_host=entre_colchetes(host) + sufixo_porta,
    )


def entre_colchetes(host: str) -> str:
    return f"[{host}]" if ":" in host else host


async def ler_limitado(response: httpx.Response) -> bytes:
    # Lê em partes e para assim que passar do limite, sem carregar a resposta inteira na memória.
    if int(response.headers.get("content-length") or 0) > MAX_BYTES:
        raise ValueError("A resposta excede o tamanho permitido.")
    corpo = bytearray()
    async for parte in response.aiter_bytes(chunk_size=65_536):
        if len(corpo) + len(parte) > MAX_BYTES:
            raise ValueError("A resposta excede o tamanho permitido.")
        corpo.extend(parte)
    return bytes(corpo)


async def ler_pagina(url: str, origem: str = "busca_web") -> Fonte:
    destino = await asyncio.to_thread(resolver_publico, url)
    # Conecta no IP validado; em HTTPS, o SNI e a verificação do certificado usam o nome original.
    extensoes = {"sni_hostname": destino.host} if urlparse(url).scheme == "https" else {}
    # trust_env=False: nenhum proxy do ambiente resolve o nome de novo por conta própria.
    async with httpx.AsyncClient(timeout=12, follow_redirects=False, headers=CABECALHOS, trust_env=False) as client:
        async with client.stream("GET", destino.url_conexao, headers={"Host": destino.cabecalho_host}, extensions=extensoes) as response:
            if response.is_redirect:
                raise ValueError("Redirecionamentos não são aceitos para fontes fornecidas.")
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", ""):
                raise ValueError("A fonte precisa ser uma página HTML pública.")
            html = (await ler_limitado(response)).decode(response.encoding or "utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
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
    async with httpx.AsyncClient(timeout=15, headers=CABECALHOS) as client:
        async with client.stream("GET", "https://api.gdeltproject.org/api/v2/doc/doc", params=params) as response:
            response.raise_for_status()
            data = json.loads(await ler_limitado(response))
    return [item["url"] for item in data.get("articles", []) if item.get("url")]


async def bing_news_urls(afirmacao: str) -> list[str]:
    params = {"q": termos_de_busca(afirmacao), "format": "rss", "mkt": "pt-BR"}
    async with httpx.AsyncClient(timeout=12, headers=CABECALHOS) as client:
        async with client.stream("GET", "https://www.bing.com/news/search", params=params) as response:
            response.raise_for_status()
            root = ElementTree.fromstring(await ler_limitado(response))
    urls = []
    for item in root.findall("./channel/item")[:8]:
        link = item.findtext("link") or ""
        # O feed usa um link de redirecionamento; a URL da fonte está no parâmetro url.
        actual = parse_qs(urlparse(link).query).get("url", [link])[0]
        if actual.startswith("https://") or actual.startswith("http://"):
            urls.append(actual)
    return urls


def checagens_do_google(data: dict) -> list[ChecagemAnterior]:
    checagens: dict[tuple[str, str], ChecagemAnterior] = {}
    for claim in data.get("claims", []):
        for review in claim.get("claimReview", []):
            url = str(review.get("url") or "")
            chave = (url, str(claim.get("text") or ""))
            if not url.startswith(("https://", "http://")) or chave in checagens:
                continue
            publisher = review.get("publisher") or {}
            checagens[chave] = ChecagemAnterior(
                agencia=str(publisher.get("name") or publisher.get("site") or urlparse(url).hostname or ""),
                data=str(review["reviewDate"])[:10] if review.get("reviewDate") else None,
                url=url,
                alegacao_checada=str(claim.get("text") or ""),
                veredito=str(review.get("textualRating") or ""),
            )
    return list(checagens.values())


async def google_fact_check(afirmacao: str) -> list[ChecagemAnterior]:
    # Usa só os dados estruturados da API; a página da checagem não vira evidência.
    if not GOOGLE_FACT_CHECK_API_KEY:
        return []
    params = {"query": termos_de_busca(afirmacao), "languageCode": "pt", "pageSize": 5, "key": GOOGLE_FACT_CHECK_API_KEY}
    async with httpx.AsyncClient(timeout=12, headers=CABECALHOS) as client:
        async with client.stream("GET", "https://factchecktools.googleapis.com/v1alpha1/claims:search", params=params) as response:
            response.raise_for_status()
            data = json.loads(await ler_limitado(response))
    return checagens_do_google(data)


async def buscar_fontes(afirmacao: str) -> tuple[list[Fonte], list[str], list[ChecagemAnterior]]:
    urls: list[str] = []
    erros: list[str] = []
    for rotulo, finder in (("Bing News", bing_news_urls), ("GDELT", gdelt_urls)):
        try:
            urls.extend(await finder(afirmacao))
        except Exception as exc:
            erros.append(f"{rotulo}: {type(exc).__name__}")
    checagens: list[ChecagemAnterior] = []
    try:
        checagens = await google_fact_check(afirmacao)
    except Exception as exc:
        erros.append(f"Google Fact Check: {type(exc).__name__}")
    unicos = list(dict.fromkeys(urls))[:10]
    paginas = await asyncio.gather(*(ler_pagina(url) for url in unicos), return_exceptions=True)
    fontes = [pagina for pagina in paginas if isinstance(pagina, Fonte)]
    if falhas := len(unicos) - len(fontes):
        erros.append(f'Afirmação "{afirmacao[:80]}": {falhas} de {len(unicos)} página(s) encontrada(s) não puderam ser lidas.')
    return fontes, erros, checagens
