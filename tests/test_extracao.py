"""Testes da extração de afirmações por regras (abordagem A, sem LLM)."""

import pytest
from fastapi.testclient import TestClient

from test_veritai import ComparadorFixo, buscador_com

from veritai import extracao as e
from veritai.api import criar_app
from veritai.relatorio import PedidoAnalise

RELEASE = """Notícias PISA Compartilhe: Compartilhe por Facebook
Publicado em 08/09/2026 13h21
No Pisa 2025, o Brasil registrou 408 pontos em Leitura, 377 em Matemática e 409 em Ciências. \
Em Leitura, a diferença passou de 102 para 58 pontos; em Matemática, de 131 para 92 pontos. \
O Dr. Silva disse que o resultado é incrível! A taxa ficou 0,15 p.p. acima de agosto. \
O ministro vai anunciar novas medidas em outubro. Isso mostra avanço."""


def test_frases_nao_partem_abreviacoes_nem_iniciais():
    assert e.frases("O Dr. Silva falou. A taxa ficou 0,15 p.p. acima. Robert E. Lee era general.") == [
        "O Dr. Silva falou.",
        "A taxa ficou 0,15 p.p. acima.",
        "Robert E. Lee era general.",
    ]
    assert e.frases("Primeiro parágrafo.\nSegundo parágrafo.") == ["Primeiro parágrafo.", "Segundo parágrafo."]


@pytest.mark.parametrize(
    ("frase", "descarte"),
    [
        ("Compartilhe por Facebook e WhatsApp agora mesmo", "rotina de página"),
        ("Publicado em 08/09/2026 13h21 por equipe", "rotina de página"),
        ("Quanto o Brasil investiu em educação em 2025?", "pergunta"),
        ("Eu acho que o resultado de 2025 foi ótimo para o país", "opinião"),
        ("É um absurdo o que fizeram com 3 milhões de pessoas", "opinião"),
        ("A medida foi bem recebida pela população local", "sem número, nome nem data"),
        ("Isso mostra avanço", "tamanho"),
    ],
)
def test_frases_descartadas(frase, descarte):
    assert e.avaliar(frase)[3] == descarte


def test_extrair_propoe_fatos_com_sinais_e_alertas():
    candidatas = e.extrair("Pisa: Brasil avança em educação", RELEASE, "2026-09-08")
    textos = [c.texto for c in candidatas]
    assert textos[0] == "No Pisa 2025, o Brasil registrou 408 pontos em Leitura, 377 em Matemática e 409 em Ciências."
    assert "número" in candidatas[0].sinais and not candidatas[0].alertas
    # Ponto e vírgula vira candidata própria, com alerta de fragmento.
    fragmento = next(c for c in candidatas if c.trecho_origem.startswith("em Matemática"))
    assert "depende da frase anterior: completar o sujeito" in fragmento.alertas
    # Mês sem ano ganha o ano pela data da notícia; o trecho de origem fica literal.
    taxa = next(c for c in candidatas if c.trecho_origem.startswith("A taxa"))
    assert taxa.texto == "A taxa ficou 0,15 p.p. acima de agosto de 2026." and taxa.trecho_origem == "A taxa ficou 0,15 p.p. acima de agosto."
    # Navegação, opinião e intenção sem dado ficam de fora.
    assert not any(t.startswith(("Notícias", "Publicado", "O Dr. Silva", "O ministro")) for t in textos)


def test_extrair_registra_atribuicao_limita_e_nao_repete_o_titulo():
    texto = "Segundo o IBGE, o IPCA de agosto de 2026 foi de -0,32%. " * 2 + " ".join(
        f"O programa atendeu {n} mil famílias em março de 2026." for n in range(20)
    )
    candidatas = e.extrair("Segundo o IBGE, o IPCA de agosto de 2026 foi de -0,32%", texto, "2026-09-10")
    assert len(candidatas) == e.MAXIMO_DE_AFIRMACOES
    assert sum(c.trecho_origem.startswith("Segundo o IBGE") for c in candidatas) == 1
    assert candidatas[0].atribuicao == "IBGE" and candidatas[0].posicao == 0


def test_datar_completa_dia_da_semana_conferindo_o_calendario():
    # 06/10/2026 é uma terça-feira; a notícia é do mesmo dia.
    assert e.datar("O prêmio foi atribuído nesta terça-feira (6).", "2026-10-06") == "O prêmio foi atribuído nesta terça-feira (6 de outubro de 2026)."
    # Dia de outro mês, perto da data: "segunda-feira (28)" numa notícia de 02/10/2026 é 28 de setembro.
    assert e.datar("A reunião foi na segunda-feira (28).", "2026-10-02") == "A reunião foi na segunda-feira (28 de setembro de 2026)."
    # Dia da semana que não bate com o calendário: fica como está.
    assert e.datar("A reunião foi na sexta-feira (6).", "2026-10-06") == "A reunião foi na sexta-feira (6)."
    assert e.datar("A reunião foi na terça-feira (6).", None) == "A reunião foi na terça-feira (6)."


def test_extrair_descarta_rodape_e_marca_data_completada():
    texto = ("Relacionadas Inflação acumula 4,33% em 12 meses Edição: Talita Compartilhe essa notícia Continuar lendo. "
             "O Inmet emitiu aviso vermelho para 12 cidades do Rio de Janeiro nesta terça-feira (6).")
    candidatas = e.extrair("", texto, "2026-10-06")
    assert [c.texto for c in candidatas] == ["O Inmet emitiu aviso vermelho para 12 cidades do Rio de Janeiro nesta terça-feira (6 de outubro de 2026)."]
    assert "data completada" in candidatas[0].sinais and not candidatas[0].alertas


def cliente():
    return TestClient(criar_app(ComparadorFixo({}), buscador_com()))


def test_endpoint_extrair_propoe_candidatas_prontas_para_o_analisar():
    noticia = {"titulo": "Pisa: Brasil avança em educação", "texto": RELEASE, "data": "2026-09-08"}
    resposta = cliente().post("/extrair", json={"noticia": noticia, "maximo": 3})
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert len(corpo["afirmacoes"]) == 3
    primeira = corpo["afirmacoes"][0]
    assert primeira["texto"].startswith("No Pisa 2025, o Brasil registrou 408 pontos")
    assert set(primeira) == {"texto", "trecho_origem", "posicao", "pontuacao", "sinais", "alertas", "atribuicao"}
    assert corpo["versoes"]["extracao"] == e.VERSAO
    assert any("confirme, edite ou descarte" in aviso for aviso in corpo["avisos"])
    # As candidatas confirmadas passam direto na validação do /analisar.
    PedidoAnalise(noticia=noticia, afirmacoes=[a["texto"] for a in corpo["afirmacoes"]], buscar_na_web=False)


def test_endpoint_extrair_avisa_sem_data_e_sem_texto_e_valida_o_pedido():
    corpo = cliente().post("/extrair", json={"noticia": {"titulo": "IPCA fica em 0,48% em setembro de 2025", "data": "11/10/2025"}}).json()
    assert [a["texto"] for a in corpo["afirmacoes"]] == ["IPCA fica em 0,48% em setembro de 2025"]
    assert any("AAAA-MM-DD" in aviso for aviso in corpo["avisos"]) and any("só o título" in aviso for aviso in corpo["avisos"])
    assert cliente().post("/extrair", json={"noticia": {"titulo": ""}}).status_code == 422
    assert cliente().post("/extrair", json={"noticia": {"titulo": "x"}, "maximo": 11}).status_code == 422


def test_candidata_longa_demais_para_o_analisar_fica_de_fora():
    longa = "O programa atendeu 3 mil famílias em março de 2026 " + "nas comunidades ribeirinhas extraordinariamente distantes " * 9 + "do país."
    assert len(longa) > e.CARACTERES_MAXIMOS and len(longa.split()) <= e.PALAVRAS_MAXIMAS
    assert e.extrair("", longa, "2026-04-01") == []


@pytest.mark.parametrize(
    ("frase", "atribuicao"),
    [
        ("Segundo o IBGE, o IPCA de agosto de 2026 foi de -0,32%.", "IBGE"),
        ("A safra deve crescer 2% em 2026, segundo dados da Conab.", "dados da Conab"),
        ("De acordo com a Anvisa, 12 lotes foram recolhidos em março de 2026.", "Anvisa"),
        # Ordinal não é atribuição.
        ("O superávit de 2026 foi o segundo maior da série, com US$ 84 bilhões.", None),
    ],
)
def test_atribuicao_distingue_fonte_de_ordinal(frase, atribuicao):
    candidatas = e.extrair("", frase, "2026-10-06")
    assert candidatas and candidatas[0].atribuicao == atribuicao
    assert ("atribuição" in candidatas[0].sinais) == (atribuicao is not None)


def test_sera_e_marca_de_futuro():
    candidata = e.extrair("", "O superávit de 2026 será o segundo maior da série, com US$ 84 bilhões.", "2026-10-06")[0]
    assert any(a.startswith("futuro ou intenção") for a in candidata.alertas)


def test_anafora_nao_confunde_nome_proprio():
    alerta = "depende da frase anterior: completar o sujeito"
    assert alerta in e.avaliar("O índice ficou em 0,48% em setembro de 2025, segundo o IBGE.")[2]
    assert alerta not in e.avaliar("O Índice Nacional de Preços ao Consumidor Amplo (IPCA) de setembro de 2025 foi 0,48%.")[2]
