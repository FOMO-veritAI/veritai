"""Testes do verificador de entidade, período e número (só rebaixa relações do NLI)."""

import pytest

from veritai import modelos as modulo_modelos
from veritai import verificador as v
from veritai.fontes import Fonte
from veritai.modelos import ComparadorNLI
from veritai.regras import agregar

APOIO = {"entailment": 0.95, "neutral": 0.03, "contradiction": 0.02}
CONTRA = {"entailment": 0.02, "neutral": 0.03, "contradiction": 0.95}


@pytest.mark.parametrize(
    ("afirmacao", "esperadas"),
    [
        ("A produção industrial de Pernambuco cresceu em agosto de 2026.", ["Pernambuco"]),
        ("O IPCA-15 de agosto de 2026 foi de -0,40%.", ["IPCA-15"]),
        ("Luiza Erundina foi eleita em São Paulo.", ["Erundina", "São", "Paulo"]),
        ("Segundo o TSE, a pausa durou 50 minutos.", ["TSE"]),
        ("Em agosto de 2026, a produção caiu 0,6%.", []),
    ],
)
def test_entidades_da_afirmacao(afirmacao, esperadas):
    assert v.entidades(afirmacao) == esperadas


def test_entidade_compara_palavra_inteira_e_aceita_dominio_e_equivalentes():
    ipca15 = "O Índice Nacional de Preços ao Consumidor Amplo 15 (IPCA-15) foi de -0,40%. O IPCA-E ficou em 0,36%."
    assert v.entidade_ausente("O IPCA de agosto foi de -0,40%.", ipca15) == "IPCA"
    assert v.entidade_ausente("O IPCA-15 de agosto foi de -0,40%.", ipca15) is None
    # A sigla da organização no domínio da fonte conta como presente ("segundo o IBGE").
    assert v.entidade_ausente("Segundo o IBGE, a safra cresceu 2%.", "A safra cresceu 2%.", dominio="agenciadenoticias.ibge.gov.br") is None
    assert v.entidade_ausente("O Brasil exportou mais.", "O país exportou mais.") is None
    assert v.entidade_ausente("A Bahia cresceu 6,5%.", "Goiás (6,5%) e Mato Grosso (5,8%).") == "Bahia"


def test_periodos_e_divergencia():
    agosto = v.periodos("A taxa no trimestre de junho a agosto de 2026 foi de 5,3%.")
    assert agosto.meses == {(8, 2026)}
    pib = v.periodos("No segundo trimestre de 2026, o PIB cresceu 0,5%. Safra 2025/26.")
    assert pib.trimestres == {(2, 2026)} and pib.ciclos == {"2025/26"} and not pib.anos_soltos
    assert v.periodo_divergente(v.periodos("O IPCA de agosto de 2026 foi -0,32%."), v.periodos("Em agosto de 2025, a variação foi -0,11%."))
    assert not v.periodo_divergente(v.periodos("O IPCA de agosto de 2026 foi -0,32%."), v.periodos("Em agosto de 2026 e julho de 2026."))
    # Afirmação anual × janela que só fala de um trimestre do mesmo ano: divergem.
    assert v.periodo_divergente(v.periodos("O PIB cresceu 0,1% em 2025."), v.periodos("No terceiro trimestre de 2025, o PIB variou 0,1%."))
    assert not v.periodo_divergente(v.periodos("O PIB cresceu 2,3% em 2025."), v.periodos("Em 2025, o PIB cresceu 2,3% frente a 2024."))
    # Janela sem período explícito não diverge de nada.
    assert not v.periodo_divergente(v.periodos("O IPCA de agosto de 2026 foi -0,32%."), v.periodos("O índice foi de -0,32%."))


def test_anotar_ano_usa_o_mes_mais_recente_ate_a_publicacao():
    assert v.anotar_ano("O IPCA de agosto foi de -0,32%, abaixo de julho.", "2026-09-11") == "O IPCA de agosto de 2026 foi de -0,32%, abaixo de julho de 2026."
    # Release de janeiro sobre dezembro: o ano é o anterior; meses que já têm ano ficam como estão.
    assert v.anotar_ano("O IPCA de dezembro teve alta. Em dezembro de 2024, foi 0,52%.", "2026-01-09") == "O IPCA de dezembro de 2025 teve alta. Em dezembro de 2024, foi 0,52%."
    # Datas com dia, marcas de futuro e fonte sem data não são anotadas.
    assert v.anotar_ano("A prova foi em 8 de novembro.", "2026-11-20") == "A prova foi em 8 de novembro."
    assert v.anotar_ano("As provas serão aplicadas em novembro.", "2026-09-18") == "As provas serão aplicadas em novembro."
    assert v.anotar_ano("O IPCA de agosto foi de -0,32%.", None) == "O IPCA de agosto foi de -0,32%."


@pytest.mark.parametrize(
    ("texto", "esperadas"),
    [
        ("foi de -0,32%, 0,39 ponto percentual (p.p.) abaixo", [(0.32, True, "pct"), (0.39, False, "pp")]),
        ("152.966 inscritos e 34,08 milhões de toneladas", [(152966.0, False, ""), (34080000.0, False, "")]),
        ("recuou –1,8% frente", [(1.8, True, "pct")]),
        # Códigos, ordinais, anos e datas não são quantidades.
        ("IPCA-15, vacina 20-valente, 13ª posição, Covid-19, em 2026, dia 8 de novembro", []),
    ],
)
def test_quantidades(texto, esperadas):
    assert [(q.valor, q.negativo, q.unidade) for q in v.quantidades(texto)] == esperadas


@pytest.mark.parametrize(
    ("afirmacao", "janela", "ausente"),
    [
        ("O IPCA de agosto foi de 0,32%.", "O IPCA de agosto foi de -0,32%.", True),  # sinal trocado
        ("Os preços caíram 0,25% em fevereiro.", "Os preços variaram -0,25%.", False),  # "caíram" = sinal negativo
        ("O IPCA de fevereiro foi de 0,37%.", "Foi de 0,70%, 0,37 ponto percentual (p.p.) acima.", True),  # % × p.p.
        ("O Enamed reuniu mais de 152 mil inscritos.", "O Enamed reúne 152.966 inscritos.", False),
        ("Foram bloqueados cerca de 1.300 sites.", "Foram bloqueados 13.241 sites.", True),
        ("O IPCA-15 foi de 0,7%.", "O IPCA-15 foi de 0,70%.", False),
        ("A produção deve atingir 34,08 milhões de toneladas.", "Deverá atingir 34,08 milhões de toneladas.", False),
        ("O público é de 60 anos ou mais.", "Pessoas com 85 anos ou mais.", True),
        # Direção: "subiram" não casa com "caiu"; "caiu para" é nível, não variação.
        ("As vendas subiram 0,8% em julho.", "As vendas caíram 0,8% em julho.", True),
        ("As vendas subiram 0,8% em julho.", "Em julho, vendas no varejo caem 0,8%.", True),
        ("O custo caiu em 25 das 27 capitais.", "O custo diminuiu em 25 das 27 capitais.", False),
        ("A taxa caiu para 5,3%.", "A taxa de desocupação (5,3%) ficou estável.", False),
        # Período: o número da janela precisa estar ligado ao período da afirmação.
        ("O IPCA de julho de 2026 foi de -0,32%.", "O IPCA de agosto de 2026 foi de -0,32%, abaixo do 0,07% registrado em julho de 2026.", True),
        ("O IPCA de julho de 2026 foi de 0,07%.", "O IPCA de agosto de 2026 foi de -0,32%, abaixo do 0,07% registrado em julho de 2026.", False),
        ("A taxa foi de 5,3% no trimestre encerrado em maio de 2026.", "A taxa (5,3%) no trimestre de junho de 2026 a agosto de 2026 caiu frente ao encerrado em maio de 2026 (5,6%).", True),
        ("A taxa foi de 5,3% no trimestre encerrado em agosto de 2026.", "Taxa é de 5,3% e subutilização é de 13,1% no trimestre encerrado em agosto de 2026.", False),
        # Período de comparação não é o período do número; "caiu de X para Y" são níveis.
        ("O superávit foi de US$ 7,74 bilhões em setembro de 2026.", "Superávit: US$ 7,74 bilhões (+146,4% ante setembro de 2025); Exportações: US$ 34,42 bilhões.", False),
        ("A diferença caiu de 102 para 58 pontos.", "A diferença passou de 102 para 58 pontos.", False),
    ],
)


def test_numero_ausente(afirmacao, janela, ausente):
    assert (v.numero_ausente(afirmacao, janela) is not None) == ausente


def test_periodo_de_comparacao_nao_torna_a_janela_divergente():
    afirmacao = "O superávit de setembro de 2026 foi o maior da história para meses de setembro."
    janela = "O superávit foi o segundo melhor para o mês, só perdendo para setembro de 2023 (US$ 9,18 bilhões)."
    assert "contradiction" not in v.bloqueios(afirmacao, janela, janela)
    # Sem comparação, um mês explícito diferente continua divergente.
    assert v.bloqueios(afirmacao, "Em setembro de 2023, o superávit foi de US$ 9,18 bilhões.", "superávit")["contradiction"] == "período divergente"


def test_bloqueios_e_ajuste_movem_probabilidade_para_neutro():
    assert v.bloqueios("A Bahia cresceu 6,5% em 2025.", "Goiás cresceu 6,5% em 2025.", "Goiás cresceu 6,5% em 2025.") == {
        "entailment": "entidade ausente: Bahia",
        "contradiction": "entidade ausente: Bahia",
    }
    # Número sem correspondente bloqueia só o apoio: a contradição continua possível.
    assert set(v.bloqueios("O IPCA de agosto de 2026 foi de 0,32%.", "O IPCA de agosto de 2026 foi de -0,32%.", "IPCA")) == {"entailment"}
    assert v.ajustar(APOIO, {"entailment": "x"}) == {"entailment": 0.0, "neutral": pytest.approx(0.98), "contradiction": 0.02}


class ModelosPorJanela:
    def __init__(self, nli):
        self.nli = nli
        self.premissas = []

    def similaridades(self, consulta, textos):
        return [0.9 for _ in textos]

    def classificar(self, pares):
        self.premissas.extend(p for p, _ in pares)
        return [next((r for chave, r in self.nli.items() if chave in p), {"entailment": 0.1, "neutral": 0.8, "contradiction": 0.1}) for p, _ in pares]


def fonte(texto, titulo="Release", dominio="agenciadenoticias.ibge.gov.br", data="2026-09-11"):
    return Fonte(url=f"https://{dominio}/x", titulo=titulo, dominio=dominio, texto=texto, origem="anexo", data_publicacao=data)


def test_comparador_neutraliza_contradicao_sobre_entidade_ausente():
    nacional = fonte("Em agosto de 2026, a produção industrial nacional recuou 0,6% frente a julho.")
    modelos = ModelosPorJanela({"recuou 0,6%": CONTRA})
    afirmacao = "A produção industrial de Pernambuco cresceu em agosto de 2026."
    assert agregar(ComparadorNLI("v1", modelos).comparar(afirmacao, [nacional])).resultado == "NOT_ENOUGH_EVIDENCE"


def test_comparador_neutraliza_meses_diferentes_e_anota_o_ano(monkeypatch):
    agosto = fonte("O IPCA de agosto foi de -0,32%.", data="2026-09-11")
    junho = fonte("O IPCA de junho foi de 0,16%.", dominio="outro.gov.br", data="2026-07-10")
    modelos = ModelosPorJanela({"agosto de 2026 foi de -0,32%": APOIO, "junho de 2026 foi de 0,16%": CONTRA})
    rastro: dict = {}
    itens = ComparadorNLI("v1", modelos).comparar("O IPCA de agosto de 2026 foi de -0,32%.", [agosto, junho], rastro)
    # O NLI recebeu as janelas com o ano; a de junho deixa de contradizer por ser de outro período.
    assert "O IPCA de agosto de 2026 foi de -0,32%." in modelos.premissas
    assert agregar(itens).resultado == "SUPPORTED"
    assert [b["bloqueios"]["contradiction"] for b in rastro["verificador"]["bloqueios"]] == ["período divergente"]
    # O texto do relatório continua o original, sem a anotação.
    assert {i.trecho for i in itens} >= {"O IPCA de agosto foi de -0,32%."}


def test_comparador_nao_aprova_numero_com_sinal_trocado():
    release = fonte("O IPCA de agosto foi de -0,32%.")
    modelos = ModelosPorJanela({"-0,32%": APOIO})
    itens = ComparadorNLI("v1", modelos).comparar("O IPCA de agosto de 2026 foi de 0,32%.", [release])
    assert agregar(itens).resultado == "NOT_ENOUGH_EVIDENCE"
    assert itens[0].bloqueios["entailment"].startswith("número sem correspondente")


def test_relatorio_registra_relacoes_neutralizadas():
    import asyncio

    from veritai.pipeline import analisar
    from veritai.relatorio import PedidoAnalise

    pedido = PedidoAnalise(
        noticia={"titulo": "Teste", "data": "2026-09-20"},
        afirmacoes=["O IPCA de agosto de 2026 foi de 0,32%."],
        evidencias=[{"titulo": "Release", "texto": "O IPCA de agosto foi de -0,32%.", "data_publicacao": "2026-09-11"}],
        buscar_na_web=False,
    )
    relatorio = asyncio.run(analisar(pedido, ComparadorNLI("v1", ModelosPorJanela({"-0,32%": APOIO}))))
    item = relatorio.afirmacoes[0]
    assert item.resultado == "NOT_ENOUGH_EVIDENCE"
    assert item.evidencias[0].relacao == "neutro"
    assert any("verificador neutralizou" in nota and "número sem correspondente" in nota for nota in item.limitacoes)


def test_verificador_desligado_mantem_o_nli(monkeypatch):
    monkeypatch.setitem(modulo_modelos.VERIFICADOR, "ativo", False)
    release = fonte("O IPCA de agosto foi de -0,32%.")
    modelos = ModelosPorJanela({"-0,32%": APOIO})
    itens = ComparadorNLI("v1", modelos).comparar("O IPCA de agosto de 2026 foi de 0,32%.", [release])
    assert agregar(itens).resultado == "SUPPORTED"
    assert modelos.premissas == ["O IPCA de agosto foi de -0,32%."]
