"""Testes do pipeline e do contrato da API, com modelos e busca simulados."""

import asyncio
import importlib.util
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from veritai import fontes as modulo_fontes
from veritai import modelos as modulo_modelos
from veritai import pipeline as modulo_pipeline
from veritai.api import criar_app
from veritai.fontes import Fonte
from veritai.config import CONFIG, nome_perfil, perfil, versoes
from veritai.modelos import ComparadorNLI, TrechoAvaliado, candidatos, com_prefixos, consolidar, dividir_em_trechos, janelas, rotulos_nli, selecionar
from veritai.regras import agregar
from veritai.relatorio import ChecagemAnterior, RelatorioAfirmacao


def fonte(dominio: str, origem: str = "busca_web") -> Fonte:
    return Fonte(url=f"https://{dominio}/materia", titulo=f"Matéria em {dominio}", dominio=dominio, texto="texto", origem=origem)


class ComparadorFixo:
    """Devolve, para cada fonte recebida, a relação configurada para o seu domínio."""

    perfil = "v0"

    def __init__(self, relacoes: dict[str, tuple[str, float, float]]):
        self.relacoes = relacoes
        self.fontes_recebidas: list[Fonte] = []

    def comparar(self, afirmacao, fontes):
        self.fontes_recebidas = list(fontes)
        itens = []
        for item in fontes:
            if item.dominio in self.relacoes:
                relacao, similaridade, nli = self.relacoes[item.dominio]
                itens.append(TrechoAvaliado(item, f"Trecho de {item.dominio}.", similaridade, relacao, nli))
        return itens


def buscador_com(*fontes: Fonte, erros: list[str] | None = None, checagens: list[ChecagemAnterior] | None = None):
    async def buscar(_afirmacao):
        return list(fontes), list(erros or []), list(checagens or [])

    return buscar


def pedido(**extra):
    corpo = {"noticia": {"titulo": "Cidade abre parques"}, "afirmacoes": ["A cidade abriu três novos parques públicos."]}
    corpo.update(extra)
    return corpo


def analisar(comparador, buscador, corpo=None):
    client = TestClient(criar_app(comparador, buscador))
    response = client.post("/analisar", json=corpo or pedido())
    assert response.status_code == 200, response.text
    return response.json()


def test_sem_evidencia_e_nao_avaliavel_e_nunca_contradicao():
    relatorio = analisar(ComparadorFixo({}), buscador_com())
    item = relatorio["afirmacoes"][0]
    assert item["resultado"] == "NOT_ENOUGH_EVIDENCE"
    assert item["avaliavel"] is False
    assert item["porcentagem"] is None
    assert item["motivo_nao_avaliavel"] == "evidencia_insuficiente"
    assert item["texto_publico"].startswith("Resultado da verificação VeritAI:")


def test_apoio_forte_nao_gera_porcentagem_sem_calibracao():
    comparador = ComparadorFixo({"a.gov.br": ("entailment", 0.8, 0.9), "b.com.br": ("entailment", 0.7, 0.85)})
    item = analisar(comparador, buscador_com(fonte("a.gov.br"), fonte("b.com.br")))["afirmacoes"][0]
    assert item["resultado"] == "SUPPORTED"
    assert item["porcentagem"] is None
    assert item["avaliavel"] is False
    assert item["motivo_nao_avaliavel"] == "modelo_nao_calibrado"
    assert item["fontes_independentes"] == 2
    assert {e["relacao"] for e in item["evidencias"]} == {"apoia"}


def test_evidencia_fraca_vira_insuficiente():
    comparador = ComparadorFixo({"a.gov.br": ("entailment", 0.40, 0.95)})
    item = analisar(comparador, buscador_com(fonte("a.gov.br")))["afirmacoes"][0]
    assert item["resultado"] == "NOT_ENOUGH_EVIDENCE"
    assert len(item["evidencias"]) == 1


def test_contradicao_e_conflito():
    so_contra = ComparadorFixo({"a.gov.br": ("contradiction", 0.8, 0.9)})
    assert analisar(so_contra, buscador_com(fonte("a.gov.br")))["afirmacoes"][0]["resultado"] == "REFUTED"
    dividido = ComparadorFixo({"a.gov.br": ("contradiction", 0.8, 0.9), "b.com.br": ("entailment", 0.8, 0.9)})
    item = analisar(dividido, buscador_com(fonte("a.gov.br"), fonte("b.com.br")))["afirmacoes"][0]
    assert item["resultado"] == "CONFLICTING_EVIDENCE"
    assert "1 fonte(s) apoiam e 1 fonte(s) contradizem" in item["justificativa"]


def test_paginas_do_mesmo_dominio_contam_uma_vez():
    copia = Fonte(url="https://a.gov.br/copia", titulo="Cópia", dominio="a.gov.br", texto="texto", origem="busca_web")
    comparador = ComparadorFixo({"a.gov.br": ("entailment", 0.8, 0.9)})
    item = analisar(comparador, buscador_com(fonte("a.gov.br"), copia))["afirmacoes"][0]
    assert item["fontes_independentes"] == 1


def test_evidencia_do_publisher_entra_sem_busca_web():
    comparador = ComparadorFixo({"documento:Relatório anual": ("entailment", 0.8, 0.9)})
    corpo = pedido(buscar_na_web=False, evidencias=[{"titulo": "Relatório anual", "texto": "Três parques foram abertos."}])
    buscador_chamado = []

    async def buscador(afirmacao):
        buscador_chamado.append(afirmacao)
        return [], [], []

    item = analisar(comparador, buscador, corpo)["afirmacoes"][0]
    assert buscador_chamado == []
    assert item["resultado"] == "SUPPORTED"
    assert item["evidencias"][0]["origem"] == "anexo"


def test_so_anexo_sem_texto_pede_conferencia_humana():
    corpo = pedido(buscar_na_web=False, anexos_sem_texto=1)
    item = analisar(ComparadorFixo({}), buscador_com(), corpo)["afirmacoes"][0]
    assert item["motivo_nao_avaliavel"] == "so_anexo_sem_texto"
    assert any("conferência humana" in texto for texto in item["limitacoes"])


def test_erros_de_busca_viram_avisos_e_versoes_sao_registradas():
    relatorio = analisar(ComparadorFixo({}), buscador_com(erros=["GDELT: TimeoutError"]))
    assert relatorio["avisos"] == ["GDELT: TimeoutError"]
    assert relatorio["versoes"]["modelo"] == "veritai-v0"
    assert set(relatorio["versoes"]) == {"modelo", "nli", "embedding", "config", "base_evidencias"}


def test_resultado_por_afirmacao_sem_media():
    comparador = ComparadorFixo({"a.gov.br": ("entailment", 0.8, 0.9)})
    corpo = pedido(afirmacoes=["Primeira afirmação.", "Segunda afirmação."])
    relatorio = analisar(comparador, buscador_com(fonte("a.gov.br")), corpo)
    assert [item["afirmacao"] for item in relatorio["afirmacoes"]] == ["Primeira afirmação.", "Segunda afirmação."]
    assert "media" not in relatorio and "porcentagem" not in relatorio


def test_textos_publicos_evitam_rotulos_isolados():
    from veritai.relatorio import TEXTO_PUBLICO

    for texto in TEXTO_PUBLICO.values():
        assert texto.startswith("Resultado da verificação VeritAI:")
        assert "Suportada" not in texto and "Refutada" not in texto


def test_pedido_invalido_e_rejeitado():
    client = TestClient(criar_app(ComparadorFixo({}), buscador_com()))
    assert client.post("/analisar", json=pedido(afirmacoes=[])).status_code == 422
    assert client.post("/analisar", json=pedido(afirmacoes=["x"] * 11)).status_code == 422


def test_health():
    client = TestClient(criar_app(ComparadorFixo({}), buscador_com()))
    corpo = client.get("/health").json()
    assert corpo["status"] == "ok"
    assert corpo["versoes"]["config"]


# Lote de correções da revisão.


def test_checagens_do_google_usam_dados_estruturados():
    data = {
        "claims": [
            {
                "text": "A cidade abriu três parques.",
                "claimReview": [
                    {
                        "publisher": {"name": "Agência Lupa", "site": "lupa.uol.com.br"},
                        "url": "https://lupa.uol.com.br/checagem",
                        "textualRating": "Falso",
                        "reviewDate": "2026-09-01T10:00:00Z",
                    },
                    {"publisher": {"name": "Sem URL"}, "textualRating": "Falso"},
                ],
            },
            {
                "text": "Outra alegação na mesma página.",
                "claimReview": [{"publisher": {"name": "Agência Lupa"}, "url": "https://lupa.uol.com.br/checagem", "textualRating": "Verdadeiro"}],
            },
        ]
    }
    assert modulo_fontes.checagens_do_google(data) == [
        ChecagemAnterior(
            agencia="Agência Lupa",
            data="2026-09-01",
            url="https://lupa.uol.com.br/checagem",
            alegacao_checada="A cidade abriu três parques.",
            veredito="Falso",
        ),
        ChecagemAnterior(
            agencia="Agência Lupa",
            data=None,
            url="https://lupa.uol.com.br/checagem",
            alegacao_checada="Outra alegação na mesma página.",
            veredito="Verdadeiro",
        ),
    ]


def test_paginas_de_checagem_nao_viram_evidencia(monkeypatch):
    checagem = ChecagemAnterior(agencia="Aos Fatos", data=None, url="https://aosfatos.org/c", alegacao_checada="x", veredito="Falso")
    lidas = []

    async def sem_urls(_afirmacao):
        return []

    async def google(_afirmacao):
        return [checagem]

    async def ler(url, origem="busca_web"):
        lidas.append(url)
        raise ValueError("não deveria ler")

    monkeypatch.setattr(modulo_fontes, "bing_news_urls", sem_urls)
    monkeypatch.setattr(modulo_fontes, "gdelt_urls", sem_urls)
    monkeypatch.setattr(modulo_fontes, "google_fact_check", google)
    monkeypatch.setattr(modulo_fontes, "ler_pagina", ler)
    encontradas, erros, checagens = asyncio.run(modulo_fontes.buscar_fontes("A cidade abriu três parques."))
    assert (encontradas, erros, checagens, lidas) == ([], [], [checagem], [])


def test_checagens_anteriores_aparecem_mas_nao_definem_resultado():
    checagem = ChecagemAnterior(
        agencia="Aos Fatos", data="2026-09-01", url="https://aosfatos.org/c", alegacao_checada="A cidade abriu três parques públicos.", veredito="Falso"
    )
    comparador = ComparadorFixo({})
    item = analisar(comparador, buscador_com(checagens=[checagem]))["afirmacoes"][0]
    assert item["resultado"] == "NOT_ENOUGH_EVIDENCE"
    assert item["checagens_anteriores"] == [checagem.model_dump()]
    assert comparador.fontes_recebidas == []
    assert any("não confirma se tratam da mesma alegação, contexto e período" in texto for texto in item["limitacoes"])


def test_sem_checagens_nao_ha_aviso_de_equivalencia():
    item = analisar(ComparadorFixo({}), buscador_com())["afirmacoes"][0]
    assert item["checagens_anteriores"] == []
    assert not any("Google Fact Check" in texto for texto in item["limitacoes"])


def test_dividir_em_trechos_preserva_evidencia_curta_do_publisher():
    assert dividir_em_trechos("Três parques foram abertos.") == []
    assert dividir_em_trechos("Três parques foram abertos.", fornecido=True) == ["Três parques foram abertos."]
    longa = "a" * 1500
    assert dividir_em_trechos(longa) == []
    assert "".join(dividir_em_trechos(longa, fornecido=True)) == longa


def test_texto_do_publisher_com_muitas_frases_nao_perde_nenhuma():
    texto = " ".join(f"Frase número {i} do relatório." for i in range(400))
    partes = dividir_em_trechos(texto, fornecido=True)
    assert " ".join(partes) == texto
    assert all(len(parte) <= 650 for parte in partes)
    assert "Frase número 399 do relatório." in partes[-1]


def test_candidatos_usam_divisao_real_por_origem():
    texto = "Três parques foram abertos. Menu."
    web = Fonte(url="https://a.com.br/x", titulo="Web", dominio="a.com.br", texto=texto, origem="busca_web")
    anexo = Fonte(url="", titulo="Relatório", dominio="documento:Relatório", texto=texto, origem="anexo")
    assert candidatos([web, anexo]) == [(anexo, "Três parques foram abertos. Menu.")]


def test_falhas_parciais_de_leitura_viram_aviso(monkeypatch):
    async def urls(_afirmacao):
        return ["https://a.com.br/1", "https://b.com.br/2", "https://c.com.br/3"]

    async def nada(_afirmacao):
        return []

    async def ler(url, origem="busca_web"):
        if url.endswith("/1"):
            return Fonte(url=url, titulo="A", dominio="a.com.br", texto="texto " * 30, origem=origem)
        raise ValueError("falhou")

    monkeypatch.setattr(modulo_fontes, "bing_news_urls", urls)
    monkeypatch.setattr(modulo_fontes, "gdelt_urls", nada)
    monkeypatch.setattr(modulo_fontes, "google_fact_check", nada)
    monkeypatch.setattr(modulo_fontes, "ler_pagina", ler)
    encontradas, erros, _ = asyncio.run(modulo_fontes.buscar_fontes("A cidade abriu três parques."))
    assert len(encontradas) == 1
    assert any("2 de 3 página(s)" in erro for erro in erros)


@pytest.mark.parametrize("afirmacao", ["Curta.", " " * 20, "x" * 501])
def test_afirmacao_fora_dos_limites_e_rejeitada(afirmacao):
    client = TestClient(criar_app(ComparadorFixo({}), buscador_com()))
    assert client.post("/analisar", json=pedido(afirmacoes=[afirmacao])).status_code == 422


@pytest.mark.parametrize("afirmacao", ["x" * 10, "x" * 500, "  " + "x" * 10 + "  "])
def test_afirmacao_nos_limites_e_aceita(afirmacao):
    relatorio = analisar(ComparadorFixo({}), buscador_com(), pedido(afirmacoes=[afirmacao]))
    assert relatorio["afirmacoes"][0]["afirmacao"] == afirmacao.strip()


@pytest.mark.parametrize(
    "url",
    ["http://[invalido", "ftp://a.com.br/x", "a.com.br/x", "https://", "https://exa mple.com/x", "https://exa_mple.com/x", "https://-a.com.br/x"],
)
def test_url_de_evidencia_invalida_gera_422(url):
    client = TestClient(criar_app(ComparadorFixo({}), buscador_com()))
    corpo = pedido(buscar_na_web=False, evidencias=[{"titulo": "Doc", "texto": "Texto.", "url": url}])
    assert client.post("/analisar", json=corpo).status_code == 422


@pytest.mark.parametrize("url", ["", "https://a.com.br/x", "http://münchen.de/x", "https://192.0.2.1/x"])
def test_url_de_evidencia_valida_e_aceita(url):
    corpo = pedido(buscar_na_web=False, evidencias=[{"titulo": "Doc", "texto": "Texto.", "url": url}])
    analisar(ComparadorFixo({}), buscador_com(), corpo)


def test_so_anexo_sem_texto_depende_de_trechos_comparaveis():
    corpo = pedido(anexos_sem_texto=1)
    com_texto = Fonte(
        url="https://a.gov.br/m", titulo="M", dominio="a.gov.br", origem="busca_web",
        texto="A prefeitura informou que inaugurou novos espaços de lazer no centro da cidade.",
    )
    item = analisar(ComparadorFixo({}), buscador_com(com_texto), corpo)["afirmacoes"][0]
    assert item["motivo_nao_avaliavel"] == "evidencia_insuficiente"
    # Página com texto, mas sem nenhum trecho que o comparador real aproveite.
    item = analisar(ComparadorFixo({}), buscador_com(fonte("a.gov.br")), corpo)["afirmacoes"][0]
    assert item["motivo_nao_avaliavel"] == "so_anexo_sem_texto"


def test_avaliavel_exige_porcentagem(monkeypatch):
    monkeypatch.setitem(modulo_pipeline.CONFIG, "calibrado", True)
    comparador = ComparadorFixo({"a.gov.br": ("entailment", 0.8, 0.9)})
    item = analisar(comparador, buscador_com(fonte("a.gov.br")))["afirmacoes"][0]
    assert item["resultado"] == "SUPPORTED"
    assert item["porcentagem"] is None and item["avaliavel"] is False
    # Sem porcentagem calculada, o único motivo disponível no contrato atual continua sendo este.
    assert item["motivo_nao_avaliavel"] == "modelo_nao_calibrado"
    with pytest.raises(ValidationError):
        RelatorioAfirmacao(
            afirmacao="x", resultado="SUPPORTED", texto_publico="x", avaliavel=True, porcentagem=None,
            motivo_nao_avaliavel=None, fontes_independentes=1, evidencias=[], checagens_anteriores=[],
            justificativa="x", limitacoes=[],
        )


def test_leitura_de_pagina_para_ao_passar_do_limite(monkeypatch):
    partes_lidas = []

    async def corpo_grande():
        for _ in range(100):
            partes_lidas.append(1)
            yield b"a" * 100_000

    def responder(_request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=corpo_grande())

    class ClienteFalso(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(responder), **kwargs)

    monkeypatch.setattr(modulo_fontes, "safe_public_url", lambda _url: True)
    monkeypatch.setattr(modulo_fontes.httpx, "AsyncClient", ClienteFalso)
    with pytest.raises(ValueError, match="excede"):
        asyncio.run(modulo_fontes.ler_pagina("https://a.com.br/grande"))
    assert len(partes_lidas) <= 17


class RespostaFalsa:
    def __init__(self, blocos, headers=None):
        self.blocos, self.headers, self.tamanhos = blocos, headers or {}, []

    async def aiter_bytes(self, chunk_size=None):
        for bloco in self.blocos:
            self.tamanhos.append(chunk_size)
            yield bloco


def test_bloco_unico_grande_e_recusado_e_leitura_usa_blocos_pequenos():
    resposta = RespostaFalsa([b"a" * 1000, b"a" * 2_000_000])
    with pytest.raises(ValueError, match="excede"):
        asyncio.run(modulo_fontes.ler_limitado(resposta))
    assert resposta.tamanhos == [65_536, 65_536]


def test_content_length_grande_e_recusado_sem_ler_corpo():
    resposta = RespostaFalsa([b"a"], headers={"content-length": "2000000"})
    with pytest.raises(ValueError, match="excede"):
        asyncio.run(modulo_fontes.ler_limitado(resposta))
    assert resposta.tamanhos == []


def test_requisicoes_pedem_resposta_sem_compressao():
    assert modulo_fontes.CABECALHOS["Accept-Encoding"] == "identity"


@pytest.mark.skipif(shutil.which("git") is None, reason="git não instalado")
def test_maestri_fica_fora_do_git():
    raiz = Path(__file__).resolve().parents[1]
    resultado = subprocess.run(["git", "check-ignore", "-q", "--no-index", ".maestri/qualquer.txt"], cwd=raiz)
    assert resultado.returncode == 0


# Baseline V1: perfis, janelas, k trechos por fonte e avaliação.

FRASES = [
    "A prefeitura abriu três novos parques públicos na zona norte da cidade.",
    "O investimento total nas obras foi de dez milhões de reais segundo a secretaria.",
    "A prefeitura negou que os parques estejam fechados para visitação neste mês.",
]


def test_janelas_de_uma_e_duas_frases():
    assert janelas(" ".join(FRASES)) == [*FRASES, f"{FRASES[0]} {FRASES[1]}", f"{FRASES[1]} {FRASES[2]}"]


def test_selecionar_k_por_fonte_max_fontes_e_minimo():
    a, b, c = fonte("a.gov.br"), fonte("b.com.br"), fonte("c.com.br")
    pares = [(a, "a1"), (a, "a2"), (a, "a3"), (b, "b1"), (b, "b2"), (c, "c1")]
    similaridades = [0.5, 0.9, 0.7, 0.6, 0.2, 0.95]
    assert selecionar(pares, similaridades, k=2, max_fontes=2, minimo=0.3) == [
        (c, [("c1", 0.95)]),
        (a, [("a2", 0.9), ("a3", 0.7)]),
    ]


def test_consolidar_guarda_maior_apoio_e_maior_contradicao_por_fonte():
    a = fonte("a.gov.br")
    avaliadas = [
        ("j1", 0.9, {"entailment": 0.80, "neutral": 0.15, "contradiction": 0.05}),
        ("j2", 0.8, {"entailment": 0.10, "neutral": 0.30, "contradiction": 0.60}),
        ("j3", 0.7, {"entailment": 0.20, "neutral": 0.70, "contradiction": 0.10}),
    ]
    itens = consolidar(a, avaliadas)
    assert [(i.trecho, i.relacao, i.pontuacao_nli) for i in itens] == [("j1", "entailment", 0.80), ("j2", "contradiction", 0.60)]
    assert itens[1].probabilidades["contradiction"] == 0.60
    # Se a mesma janela tem o maior apoio e a maior contradição, ela aparece uma vez só.
    assert len(consolidar(a, avaliadas[:1])) == 1


class ModelosFalsos:
    """Similaridade por palavra-chave e NLI configurado por janela; registra o que foi pedido."""

    def __init__(self, nli: dict[str, dict[str, float]]):
        self.nli = nli
        self.pedidos_nli: list[tuple[str, str]] = []

    def similaridades(self, consulta, textos):
        return [0.9 if "parques" in texto else 0.5 if "prefeitura" in texto else 0.1 for texto in textos]

    def classificar(self, pares):
        self.pedidos_nli.extend(pares)
        neutro = {"entailment": 0.1, "neutral": 0.8, "contradiction": 0.1}
        return [self.nli.get(janela, neutro) for janela, _ in pares]


def test_comparador_leva_k_janelas_por_fonte_ao_nli(monkeypatch):
    monkeypatch.setitem(modulo_modelos.SELECAO, "trechos_por_fonte", 3)
    texto = " ".join(FRASES)
    apoio = FRASES[0]
    # Similaridades falsas: F0, F2 e a janela F0+F1 ficam entre as 3 mais similares; F1+F2 fica de fora.
    contra = f"{FRASES[0]} {FRASES[1]}"
    modelos = ModelosFalsos({apoio: {"entailment": 0.9, "neutral": 0.05, "contradiction": 0.05}, contra: {"entailment": 0.05, "neutral": 0.15, "contradiction": 0.8}})
    pagina = Fonte(url="https://a.gov.br/1", titulo="A", dominio="a.gov.br", texto=texto, origem="busca_web")
    copia = Fonte(url="https://a.gov.br/2", titulo="A2", dominio="a.gov.br", texto=texto, origem="busca_web")
    comparador = ComparadorNLI("v0", modelos)
    itens = comparador.comparar("A cidade abriu três parques.", [pagina, copia])
    # k = 3 janelas por fonte, duas fontes: 6 pares levados ao NLI, cada um como (janela, afirmação).
    assert len(modelos.pedidos_nli) == 6
    assert all(hipotese == "A cidade abriu três parques." for _, hipotese in modelos.pedidos_nli)
    assert {(i.fonte.url, i.trecho, i.relacao) for i in itens} == {
        (pagina.url, apoio, "entailment"), (pagina.url, contra, "contradiction"),
        (copia.url, apoio, "entailment"), (copia.url, contra, "contradiction"),
    }
    # Duas páginas do mesmo domínio continuam contando como uma fonte.
    assert agregar(itens).fontes_independentes == 1


def test_comparador_sem_janela_similar_nao_chama_nli():
    modelos = ModelosFalsos({})
    comparador = ComparadorNLI("v0", modelos)
    assert comparador.comparar("x", [Fonte(url="", titulo="d", dominio="d", texto="Nada relacionado aqui.", origem="anexo")]) == []
    assert modelos.pedidos_nli == []


def test_rotulos_nli_vem_do_config_do_modelo():
    # Ordem do config.json de MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7.
    assert rotulos_nli({"0": "entailment", "1": "neutral", "2": "contradiction"}) == {0: "entailment", 1: "neutral", 2: "contradiction"}
    assert rotulos_nli({0: "CONTRADICTION", 1: "NEUTRAL", 2: "ENTAILMENT"}) == {0: "contradiction", 1: "neutral", 2: "entailment"}
    with pytest.raises(ValueError):
        rotulos_nli({0: "LABEL_0", 1: "LABEL_1", 2: "LABEL_2"})


def test_perfil_por_variavel_de_ambiente(monkeypatch):
    monkeypatch.delenv("VERITAI_PERFIL", raising=False)
    assert nome_perfil() == CONFIG["perfil_padrao"] == "v0"
    monkeypatch.setenv("VERITAI_PERFIL", "v1")
    assert nome_perfil() == "v1"
    assert versoes()["modelo"] == "veritai-v1"
    assert versoes()["nli"] == "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
    assert versoes()["embedding"] == "intfloat/multilingual-e5-base"
    assert ComparadorNLI(modelos=ModelosFalsos({})).perfil == "v1"
    monkeypatch.setenv("VERITAI_PERFIL", "v9")
    with pytest.raises(ValueError, match="Perfil"):
        nome_perfil()


def test_prefixos_do_e5_so_na_v1():
    assert com_prefixos(perfil("v1"), "afirmação", ["trecho"]) == ["query: afirmação", "passage: trecho"]
    assert com_prefixos(perfil("v0"), "afirmação", ["trecho"]) == ["afirmação", "trecho"]


def test_relatorio_e_health_registram_o_perfil_usado():
    comparador = ComparadorFixo({})
    comparador.perfil = "v1"
    relatorio = analisar(comparador, buscador_com())
    assert relatorio["versoes"]["modelo"] == "veritai-v1"
    health = TestClient(criar_app(comparador, buscador_com())).get("/health").json()
    assert health["perfil"] == "v1" and health["versoes"]["modelo"] == "veritai-v1"


def test_limiares_marcados_como_nao_validados():
    assert "não validados para nenhum perfil" in CONFIG["limiares_triagem"]["_nota"]
    assert CONFIG["selecao"]["trechos_por_fonte"] == 3 and CONFIG["selecao"]["max_fontes_por_afirmacao"] == 6


def carregar_avaliar():
    caminho = Path(__file__).resolve().parents[1] / "eval" / "avaliar.py"
    especificacao = importlib.util.spec_from_file_location("avaliar", caminho)
    modulo = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(modulo)
    return modulo


def test_metricas_do_eval_com_numeros_conhecidos():
    avaliar = carregar_avaliar()
    rotulos = ["SUPPORTED", "SUPPORTED", "REFUTED", "REFUTED", "NOT_ENOUGH_EVIDENCE"]
    preditos = ["SUPPORTED", "REFUTED", "SUPPORTED", "REFUTED", "NOT_ENOUGH_EVIDENCE"]
    assert avaliar.f1_por_classe(rotulos, preditos) == {"SUPPORTED": 0.5, "REFUTED": 0.5, "NOT_ENOUGH_EVIDENCE": 1.0, "CONFLICTING_EVIDENCE": None}
    assert avaliar.macro_f1(rotulos, preditos) == pytest.approx(2 / 3)
    assert avaliar.taxa_falsas_aprovacoes(rotulos, preditos) == pytest.approx(1 / 3)
    matriz = avaliar.matriz_confusao(rotulos, preditos)
    assert matriz["SUPPORTED"]["REFUTED"] == 1 and matriz["REFUTED"]["SUPPORTED"] == 1 and matriz["NOT_ENOUGH_EVIDENCE"]["NOT_ENOUGH_EVIDENCE"] == 1
    assert avaliar.taxa_falsas_aprovacoes(["SUPPORTED"], ["SUPPORTED"]) is None
    # CONFLICTING_EVIDENCE previsto como SUPPORTED também é falsa aprovação.
    assert avaliar.taxa_falsas_aprovacoes(["CONFLICTING_EVIDENCE"], ["SUPPORTED"]) == 1.0
    rotulos = ["SUPPORTED", "REFUTED", "NOT_ENOUGH_EVIDENCE", "CONFLICTING_EVIDENCE", "CONFLICTING_EVIDENCE"]
    preditos = ["SUPPORTED", "REFUTED", "SUPPORTED", "SUPPORTED", "CONFLICTING_EVIDENCE"]
    assert avaliar.taxa_falsas_aprovacoes(rotulos, preditos) == pytest.approx(2 / 4)


def test_bootstrap_e_deterministico_e_contem_a_estimativa():
    avaliar = carregar_avaliar()
    rotulos = ["SUPPORTED", "REFUTED", "NOT_ENOUGH_EVIDENCE"] * 10
    assert avaliar.bootstrap(avaliar.macro_f1, rotulos, rotulos) == (1.0, 1.0)
    preditos = ["SUPPORTED"] * 30
    intervalo = avaliar.bootstrap(avaliar.taxa_falsas_aprovacoes, rotulos, preditos, repeticoes=200, semente=1)
    assert intervalo == avaliar.bootstrap(avaliar.taxa_falsas_aprovacoes, rotulos, preditos, repeticoes=200, semente=1)
    assert intervalo == (1.0, 1.0)


def test_contraste_tem_formato_valido_e_e_marcado_como_sintetico():
    avaliar = carregar_avaliar()
    itens = avaliar.carregar(Path(__file__).resolve().parents[1] / "eval" / "contraste.jsonl")
    assert 20 <= len(itens) <= 30
    assert {item["fonte"] for item in itens} == {"contraste_sintetico"}
    assert {item["rotulo"] for item in itens} == {"SUPPORTED", "REFUTED", "NOT_ENOUGH_EVIDENCE"}


def test_carregar_rejeita_linha_sem_campos(tmp_path):
    avaliar = carregar_avaliar()
    arquivo = tmp_path / "x.jsonl"
    arquivo.write_text('{"afirmacao": "a", "rotulo": "SUPPORTED"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="campos ausentes"):
        avaliar.carregar(arquivo)


def test_eval_conta_fonte_independente_como_o_servico():
    avaliar = carregar_avaliar()
    item = {"evidencias": [
        {"texto": "t", "fonte": "Portal A", "url": "https://a.gov.br/1"},
        {"texto": "t", "fonte": "Portal A (cópia)", "url": "https://a.gov.br/2"},
        {"texto": "t", "fonte": "Relatório"},
    ]}
    assert [f.dominio for f in avaliar.fontes_do_item(item)] == ["a.gov.br", "a.gov.br", "documento:Relatório"]
    comparador = ComparadorFixo({"a.gov.br": ("entailment", 0.8, 0.9)})
    assert agregar(comparador.comparar("A", avaliar.fontes_do_item(item))).fontes_independentes == 1


def test_selecao_prioriza_dominios_diferentes():
    paginas = [Fonte(url=f"https://a.gov.br/{i}", titulo="A", dominio="a.gov.br", texto="t", origem="busca_web") for i in range(6)]
    outra = fonte("b.com.br")
    pares = [(pagina, f"a{i}") for i, pagina in enumerate(paginas)] + [(outra, "b")]
    similaridades = [0.9, 0.89, 0.88, 0.87, 0.86, 0.85, 0.5]
    selecionadas = selecionar(pares, similaridades, k=3, max_fontes=6, minimo=0.3)
    assert [f.dominio for f, _ in selecionadas] == ["a.gov.br", "b.com.br", "a.gov.br", "a.gov.br", "a.gov.br", "a.gov.br"]


def test_eval_prever_usa_regras_do_servico():
    avaliar = carregar_avaliar()
    item = {"id": "x", "par": "P", "afirmacao": "A", "rotulo": "SUPPORTED", "evidencias": [{"texto": "t", "fonte": "Fonte A"}]}
    comparador = ComparadorFixo({"documento:Fonte A": ("entailment", 0.8, 0.9)})
    assert avaliar.prever([item], comparador)[0]["predito"] == "SUPPORTED"
    assert comparador.fontes_recebidas[0].origem == "anexo"
