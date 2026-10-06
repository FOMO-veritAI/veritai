"""Testes do pipeline e do contrato da API, com modelos e busca simulados."""

from fastapi.testclient import TestClient

from veritai.api import criar_app
from veritai.fontes import Fonte
from veritai.modelos import TrechoAvaliado


def fonte(dominio: str, origem: str = "busca_web") -> Fonte:
    return Fonte(url=f"https://{dominio}/materia", titulo=f"Matéria em {dominio}", dominio=dominio, texto="texto", origem=origem)


class ComparadorFixo:
    """Devolve, para cada fonte recebida, a relação configurada para o seu domínio."""

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


def buscador_com(*fontes: Fonte, erros: list[str] | None = None):
    async def buscar(_afirmacao):
        return list(fontes), list(erros or [])

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
        return [], []

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
