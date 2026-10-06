"""Testes do rastro interno do comparador, da explicação das regras e da montagem da demonstração (modelos simulados)."""

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

from test_veritai import ComparadorFixo, FRASES, ModelosFalsos, buscador_com, pedido

from veritai.api import criar_app
from veritai.fontes import Fonte
from veritai.modelos import ComparadorNLI, TrechoAvaliado
from veritai.regras import explicar

APOIO = {"entailment": 0.9, "neutral": 0.05, "contradiction": 0.05}
CONTRA = {"entailment": 0.05, "neutral": 0.15, "contradiction": 0.8}


def fontes_de_teste():
    texto = " ".join(FRASES)
    web = Fonte(url="https://a.gov.br/1", titulo="A", dominio="a.gov.br", texto=texto, origem="busca_web", grupo="grupo:a.gov.br")
    anexo = Fonte(url="", titulo="Anexo", dominio="documento:Anexo", texto="Texto sem relação nenhuma.", origem="anexo")
    return web, anexo


def test_rastro_registra_trechos_janelas_similaridade_e_nli():
    web, anexo = fontes_de_teste()
    modelos = ModelosFalsos({FRASES[0]: APOIO, f"{FRASES[0]} {FRASES[1]}": CONTRA})
    rastro = {}
    itens = ComparadorNLI("v0", modelos).comparar("A cidade abriu três parques.", [web, anexo], rastro=rastro)
    assert rastro["perfil"] == "v0" and rastro["parametros"]["trechos_por_fonte"] == 3
    fonte_web, fonte_anexo = rastro["fontes"]
    assert fonte_web["trechos"] == FRASES and fonte_web["grupo"] == "grupo:a.gov.br"
    assert [j["partes"] for j in fonte_web["janelas"]] == [1, 1, 1, 2, 2]
    assert [j["frases"] for j in fonte_web["janelas"]] == [1, 1, 1, 2, 2]
    levadas = [j for j in fonte_web["janelas"] if j["levada_ao_nli"]]
    # As 3 janelas mais similares (k = 3) foram ao NLI, com as probabilidades registradas.
    assert len(levadas) == 3 and all(j["nli"] for j in levadas)
    assert {j["texto"] for j in fonte_web["janelas"] if j["no_relatorio"]} == {item.trecho for item in itens}
    # O anexo tem similaridade baixa: nenhuma janela vai ao NLI.
    assert fonte_anexo["janelas"] and not any(j["levada_ao_nli"] for j in fonte_anexo["janelas"])
    assert set(rastro["tempos_s"]) == {"divisao_em_janelas", "similaridade", "selecao", "nli", "consolidacao"}


def test_resultado_e_o_mesmo_com_e_sem_rastro():
    web, anexo = fontes_de_teste()
    modelos = ModelosFalsos({FRASES[0]: APOIO})
    comparador = ComparadorNLI("v0", modelos)
    sem = comparador.comparar("A cidade abriu três parques.", [web, anexo])
    com = comparador.comparar("A cidade abriu três parques.", [web, anexo], rastro={})
    assert [(i.trecho, i.relacao, i.pontuacao_nli) for i in sem] == [(i.trecho, i.relacao, i.pontuacao_nli) for i in com]


def test_rastro_sem_texto_comparavel():
    rastro = {}
    vazio = Fonte(url="https://a.com.br", titulo="A", dominio="a.com.br", texto="Curto.", origem="busca_web")
    assert ComparadorNLI("v0", ModelosFalsos({})).comparar("x", [vazio], rastro=rastro) == []
    assert rastro["fontes"][0]["janelas"] == [] and set(rastro["tempos_s"]) == {"divisao_em_janelas"}


def item(dominio, relacao, similaridade, nli):
    return TrechoAvaliado(Fonte(url="", titulo=dominio, dominio=dominio, texto="t", origem="anexo"), f"trecho {dominio}", similaridade, relacao, nli)


def test_explicar_cada_regra_de_agregacao():
    casos = {
        "SUPPORTED": [item("a", "entailment", 0.8, 0.9)],
        "REFUTED": [item("a", "contradiction", 0.8, 0.9)],
        "CONFLICTING_EVIDENCE": [item("a", "entailment", 0.8, 0.9), item("b", "contradiction", 0.8, 0.9)],
        "NOT_ENOUGH_EVIDENCE": [item("a", "entailment", 0.40, 0.95), item("b", "contradiction", 0.8, 0.5)],
    }
    for esperado, itens in casos.items():
        explicacao = explicar(itens)
        assert explicacao["resultado"] == esperado and explicacao["regra"]
    fraco = explicar(casos["NOT_ENOUGH_EVIDENCE"])["itens"]
    assert [(i["passa_similaridade_forte"], i["passa_nli_forte"], i["forte"]) for i in fraco] == [(False, True, False), (True, False, False)]
    assert explicar(casos["CONFLICTING_EVIDENCE"])["grupos_a_favor"] == ["a"]
    assert [i["conta_como"] for i in explicar(casos["CONFLICTING_EVIDENCE"])["itens"]] == ["apoio", "contradicao"]
    neutro_forte = explicar([item("a", "neutral", 0.9, 0.95)])
    assert neutro_forte["itens"][0]["forte"] and neutro_forte["itens"][0]["conta_como"] is None
    assert neutro_forte["resultado"] == "NOT_ENOUGH_EVIDENCE"


def test_saida_da_api_nao_ganha_campos_de_rastro():
    comparador = ComparadorFixo({"a.gov.br": ("entailment", 0.8, 0.9)})
    relatorio = TestClient(criar_app(comparador, buscador_com())).post("/analisar", json=pedido()).json()
    assert set(relatorio) == {"afirmacoes", "avisos", "versoes"}
    assert "rastro" not in relatorio["afirmacoes"][0]


def carregar_demo():
    caminho = Path(__file__).resolve().parents[1] / "demo" / "gerar_demo.py"
    especificacao = importlib.util.spec_from_file_location("gerar_demo", caminho)
    modulo = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(modulo)
    return modulo


def test_demo_monta_uma_passada_com_modelos_simulados():
    demo = carregar_demo()
    caso = demo.CASOS[1]
    contra = caso["pedido"]["evidencias"][1]["texto"].split(". ")[0] + "."
    passada = demo.rodar_caso(caso, ComparadorNLI("v0", ModelosFalsosDemo(contra)))
    assert passada["perfil"] == "v0" and passada["resultado_obtido"] == passada["relatorio_api"]["afirmacoes"][0]["resultado"]
    assert passada["agregacao"]["resultado"] == passada["resultado_obtido"]
    tempos = passada["tempos_s"]
    # As etapas somam o total da requisição; a medição isolada das regras fica fora dessa conta.
    etapas = sum(valor for chave, valor in tempos.items() if chave != "requisicao_total")
    assert abs(etapas - tempos["requisicao_total"]) < 1e-9
    assert "regras" not in tempos and passada["tempo_isolado_das_regras_s"] >= 0
    assert len(passada["rastro"]["fontes"]) == 2 and passada["grupos_de_fontes_independentes"]


def test_casos_da_demo_sao_validos_e_ficticios():
    demo = carregar_demo()
    assert [c["esperado"] for c in demo.CASOS] == ["SUPPORTED", "REFUTED", "NOT_ENOUGH_EVIDENCE"]
    for caso in demo.CASOS:
        assert caso["pedido"]["buscar_na_web"] is False and caso["por_que"]
        assert all(not e.get("url") or e["url"].split("/")[2].endswith(".example") for e in caso["pedido"]["evidencias"])
        assert TestClient(criar_app(ComparadorFixo({}), buscador_com())).post("/analisar", json=caso["pedido"]).status_code == 200


def test_area_dos_testes_por_palavra_chave():
    demo = carregar_demo()
    assert demo.area_do_teste("test_conexao_usa_o_ip_validado_mesmo_se_o_dns_mudar") == "Segurança de URLs e leitura de páginas"
    assert demo.area_do_teste("test_rastro_sem_texto_comparavel") == "Demonstração (rastro e explicação)"
    assert demo.area_do_teste("test_health") == demo.OUTROS


class ModelosFalsosDemo(ModelosFalsos):
    """Similaridade alta para tudo e contradição forte numa frase escolhida."""

    def __init__(self, frase_contra: str):
        super().__init__({frase_contra: CONTRA})

    def similaridades(self, consulta, textos):
        return [0.9] * len(textos)


def test_rastro_com_janelas_repetidas_marca_so_as_ocorrencias_levadas():
    repetida = "A prefeitura abriu três novos parques públicos na zona norte da cidade."
    fonte = Fonte(url="https://a.gov.br/1", titulo="A", dominio="a.gov.br", texto=" ".join([repetida] * 5), origem="busca_web")
    rastro = {}
    itens = ComparadorNLI("v0", ModelosFalsos({repetida: APOIO})).comparar("A cidade abriu três parques.", [fonte], rastro=rastro)
    janelas = rastro["fontes"][0]["janelas"]
    assert len(janelas) == 9
    assert sum(j["levada_ao_nli"] for j in janelas) == 3
    assert sum(j["no_relatorio"] for j in janelas) == len(itens) == 1
    # Só as primeiras ocorrências (seleção estável) aparecem como levadas ao NLI.
    assert [j["levada_ao_nli"] for j in janelas[:3]] == [True, True, True]


def test_rastro_conta_frases_reais_em_partes_agrupadas_do_publisher():
    anexo = Fonte(url="", titulo="Nota", dominio="documento:Nota", texto="Primeira frase. Segunda frase. Terceira frase.", origem="anexo")
    rastro = {}
    ComparadorNLI("v0", ModelosFalsos({})).comparar("x", [anexo], rastro=rastro)
    fonte = rastro["fontes"][0]
    assert fonte["trechos"] == ["Primeira frase. Segunda frase. Terceira frase."]
    assert fonte["janelas"][0]["partes"] == 1 and fonte["janelas"][0]["frases"] == 3
    assert "agrupadas" in fonte["divisao"]
