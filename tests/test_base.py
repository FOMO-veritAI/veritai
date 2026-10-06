"""Testes da base própria com documentos sintéticos, embedding simulado e banco temporário."""

import json
import re
import zlib

import numpy
import pytest
from fastapi.testclient import TestClient

from test_veritai import ComparadorFixo, buscador_com, pedido

from veritai.api import criar_app
from veritai.base import BaseEvidencias, BaseNaoIndexada
from veritai.base import importar, trechos
from veritai.base.__main__ import main
from veritai.base.busca import consulta_fts, rrf
from veritai.copias import agrupar
from veritai.fontes import Fonte

DIMENSAO = 64


def vetorizar(textos, _tipo):
    """Saco de palavras com hash: textos com palavras em comum ficam próximos."""
    matriz = numpy.zeros((len(textos), DIMENSAO), dtype=numpy.float32)
    for linha, texto in enumerate(textos):
        for palavra in re.findall(r"\w+", texto.lower()):
            matriz[linha, zlib.crc32(palavra.encode()) % DIMENSAO] += 1
    normas = numpy.linalg.norm(matriz, axis=1, keepdims=True)
    return matriz / numpy.where(normas == 0, 1, normas)


PARQUES = (
    "A prefeitura inaugurou três parques públicos na zona norte da cidade em março. "
    "As obras custaram dez milhões de reais, segundo a secretaria de obras. "
    "Os parques têm pistas de caminhada, quadras e áreas para cães."
)
# Matéria do tamanho de uma notícia curta: em textos de poucas dezenas de palavras, uma troca já derruba o Jaccard.
MATERIA = PARQUES + (
    " A inauguração contou com a presença de moradores e de representantes das associações de bairro."
    " Segundo a prefeitura, os novos espaços vão funcionar todos os dias, das seis da manhã às dez da noite."
    " A segurança será feita pela guarda municipal, com rondas a pé e câmeras ligadas a uma central de monitoramento."
    " A secretaria informou que outros dois parques estão em obras na zona sul e devem ser entregues no segundo semestre."
    " Moradores ouvidos pela reportagem elogiaram as pistas, mas cobraram mais bebedouros e banheiros públicos."
    " A prefeitura disse que vai instalar novos bebedouros até o fim do ano, sem informar o custo dessa etapa."
)
VACINA = (
    "A campanha de vacinação contra a gripe começou em abril em todos os postos de saúde. "
    "A meta é vacinar noventa por cento dos idosos até o fim de maio."
)


def escrever(pasta, nome, texto):
    arquivo = pasta / f"{nome}.txt"
    arquivo.write_text(texto, encoding="utf-8")
    return arquivo


@pytest.fixture
def banco(tmp_path):
    return tmp_path / "base.sqlite3"


def carregar(banco, tmp_path, textos: dict[str, str], data=None, indexar=True):
    resultado = importar.importar_textos(banco, [escrever(tmp_path, nome, texto) for nome, texto in textos.items()], data)
    if indexar:
        importar.indexar(banco, "embedding-teste", vetorizar)
    return resultado


def base_de(banco, embedding="embedding-teste"):
    return BaseEvidencias(banco, embedding, vetorizar)


def test_trechos_de_150_palavras_com_sobreposicao_sem_partir_frases():
    frase = "Frase número {} tem exatamente dez palavras para o teste."
    texto = " ".join(frase.format(i) for i in range(20))
    partes = trechos.dividir(texto, palavras=150, sobreposicao=30)
    assert len(partes[0].split()) == 150
    assert all(parte.endswith("teste.") and parte.startswith("Frase número") for parte in partes)
    # As 3 últimas frases (30 palavras) do primeiro trecho abrem o segundo.
    assert partes[1].startswith(frase.format(12)) and frase.format(14) in partes[0]
    assert frase.format(19) in partes[-1]
    longa = " ".join(["palavra"] * 200) + "."
    assert trechos.dividir(longa, palavras=150, sobreposicao=30) == [longa]


def test_consulta_fts_nao_vira_operador():
    assert consulta_fts('parques "OR" NEAR(abertos) -- da') == '"parques" OR "near" OR "abertos"'
    assert consulta_fts("de da do") is None


def test_rrf_com_numeros_conhecidos():
    # 1: 1/61 + 1/62; 3: 1/63 + 1/61; 2: 1/62.
    assert rrf([[1, 2, 3], [3, 1]], k=60) == [1, 3, 2]


def test_bm25_e_busca_vetorial_encontram_o_documento_certo(banco, tmp_path):
    carregar(banco, tmp_path, {"Parques": PARQUES, "Vacina": VACINA})
    base = base_de(banco)
    consulta = "A cidade inaugurou três parques públicos"
    primeiro_bm25 = base.buscar_bm25(consulta)[0]
    primeiro_vetor = base.buscar_vetorial(consulta)[0]
    fontes = base.buscar(consulta)
    assert primeiro_bm25 == primeiro_vetor
    assert fontes[0].titulo == "Parques (base própria, documento 1)" and fontes[0].origem == "base_propria"
    assert fontes[0].texto_completo == PARQUES


def test_copias_quase_literais_ficam_no_mesmo_grupo(banco, tmp_path):
    copia = MATERIA.replace("três parques", "3 parques")
    carregar(banco, tmp_path, {"Parques": MATERIA, "Parques (cópia)": copia, "Vacina": VACINA})
    estatisticas = importar.estatisticas(banco)
    assert estatisticas["documentos"] == 3 and estatisticas["grupos_de_copias"] == 2


def test_agrupamento_vale_para_todas_as_origens():
    web = Fonte(url="https://a.com.br/1", titulo="A", dominio="a.com.br", texto=MATERIA, origem="busca_web")
    mesma_pagina_outro_site = Fonte(url="https://b.com.br/1", titulo="B", dominio="b.com.br", texto=MATERIA, origem="busca_web")
    anexo = Fonte(url="", titulo="Relatório", dominio="documento:Relatório", texto=MATERIA.replace("dez milhões", "10 milhões"), origem="anexo")
    outra = Fonte(url="https://c.com.br/1", titulo="C", dominio="c.com.br", texto=VACINA, origem="busca_web")
    mesmo_dominio = Fonte(url="https://c.com.br/2", titulo="C2", dominio="c.com.br", texto="Outro texto qualquer do mesmo site.", origem="busca_web")
    fontes = [web, mesma_pagina_outro_site, anexo, outra, mesmo_dominio]
    agrupar(fontes)
    assert web.grupo == mesma_pagina_outro_site.grupo == anexo.grupo
    assert outra.grupo == mesmo_dominio.grupo != web.grupo


def test_copias_contam_como_uma_fonte_no_relatorio():
    comparador = ComparadorFixo({"a.com.br": ("entailment", 0.8, 0.9), "b.com.br": ("entailment", 0.8, 0.9)})
    a = Fonte(url="https://a.com.br/1", titulo="A", dominio="a.com.br", texto=PARQUES, origem="busca_web")
    b = Fonte(url="https://b.com.br/1", titulo="B", dominio="b.com.br", texto=PARQUES, origem="busca_web")
    item = TestClient(criar_app(comparador, buscador_com(a, b))).post("/analisar", json=pedido()).json()["afirmacoes"][0]
    assert item["resultado"] == "SUPPORTED" and item["fontes_independentes"] == 1
    assert any("cópias só detecta textos quase literais" in texto for texto in item["limitacoes"])


def fonte_datada(dominio, data):
    return Fonte(url=f"https://{dominio}/m", titulo=dominio, dominio=dominio, texto=f"texto de {dominio}", origem="busca_web", data_publicacao=data)


def test_filtro_de_data_por_dia_em_todas_as_origens():
    comparador = ComparadorFixo({})
    buscador = buscador_com(fonte_datada("mesmo-dia.com.br", "2026-03-10T23:00:00"), fonte_datada("depois.com.br", "2026-03-11"), fonte_datada("sem-data.com.br", None))
    corpo = pedido(noticia={"titulo": "Cidade abre parques", "data": "2026-03-10"},
                   evidencias=[{"titulo": "Anexo posterior", "texto": "Texto do anexo.", "data_publicacao": "2026-04-01"}])
    item = TestClient(criar_app(comparador, buscador)).post("/analisar", json=corpo).json()["afirmacoes"][0]
    assert {f.dominio for f in comparador.fontes_recebidas} == {"mesmo-dia.com.br", "sem-data.com.br"}
    assert any("2 fonte(s) publicadas depois da data da notícia (2026-03-10)" in t for t in item["limitacoes"])
    assert any("1 fonte(s) sem data de publicação" in t for t in item["limitacoes"])


def test_data_da_noticia_ilegivel_nao_filtra_e_vira_limitacao():
    comparador = ComparadorFixo({})
    corpo = pedido(noticia={"titulo": "Cidade abre parques", "data": "10/03/2026"})
    item = TestClient(criar_app(comparador, buscador_com(fonte_datada("depois.com.br", "2026-03-11")))).post("/analisar", json=corpo).json()["afirmacoes"][0]
    assert [f.dominio for f in comparador.fontes_recebidas] == ["depois.com.br"]
    assert any("não está no formato AAAA-MM-DD" in t for t in item["limitacoes"])


def test_base_filtra_por_data_na_busca(banco, tmp_path):
    carregar(banco, tmp_path, {"Parques": PARQUES}, data="2026-03-20")
    from datetime import date

    assert base_de(banco).buscar("parques públicos", date(2026, 3, 19)) == []
    assert base_de(banco).buscar("parques públicos", date(2026, 3, 20))


def test_perfil_errado_da_erro_claro(banco, tmp_path):
    carregar(banco, tmp_path, {"Parques": PARQUES})
    outro = base_de(banco, embedding="outro-embedding")
    with pytest.raises(BaseNaoIndexada, match="outro-embedding.*embedding-teste.*indexar --perfil"):
        outro.buscar("parques")
    with pytest.raises(BaseNaoIndexada):
        criar_app(ComparadorFixo({}), buscador_com(), outro)


def test_documento_novo_sem_vetor_tambem_bloqueia(banco, tmp_path):
    carregar(banco, tmp_path, {"Parques": PARQUES})
    carregar(banco, tmp_path, {"Vacina": VACINA}, indexar=False)
    with pytest.raises(BaseNaoIndexada, match="1 trecho"):
        base_de(banco).buscar("vacina")


def test_relatorio_usa_base_propria_e_registra_snapshot(banco, tmp_path):
    resultado = carregar(banco, tmp_path, {"Parques": PARQUES, "Vacina": VACINA})
    comparador = ComparadorFixo({f"documento:{(tmp_path / 'Parques.txt').resolve()}": ("entailment", 0.8, 0.9)})
    client = TestClient(criar_app(comparador, buscador_com(), base_de(banco)))
    relatorio = client.post("/analisar", json=pedido(buscar_na_web=False)).json()
    item = relatorio["afirmacoes"][0]
    assert item["resultado"] == "SUPPORTED"
    assert {e["origem"] for e in item["evidencias"]} == {"base_propria"}
    assert relatorio["versoes"]["base_evidencias"] == resultado.carga
    assert client.get("/health").json()["versoes"]["base_evidencias"] == resultado.carga
    assert not any("ainda não são consultadas em uma base própria" in t for t in item["limitacoes"])


def test_carga_sem_conteudo_novo_nao_muda_snapshot(banco, tmp_path):
    primeira = carregar(banco, tmp_path, {"Parques": PARQUES})
    segunda = carregar(banco, tmp_path, {"Parques": PARQUES})
    assert (segunda.inseridos, segunda.repetidos, segunda.carga) == (0, 1, None)
    assert base_de(banco).snapshot == primeira.carga


CLAIMS = {
    "claims": [
        {
            "text": "A cidade abriu três parques públicos em março.",
            "claimReview": [{"publisher": {"name": "Agência X"}, "url": "https://agencia-x.org/c1", "textualRating": "Exagerado", "reviewDate": "2026-03-15T00:00:00Z"}],
        },
        {
            "text": "A vacina da gripe causa a doença.",
            "claimReview": [{"publisher": {"name": "Agência Y"}, "url": "https://agencia-y.org/c2", "textualRating": "Falso"}],
        },
    ]
}


def test_checagens_guardam_veredito_original_e_so_informam(banco, tmp_path):
    arquivo = tmp_path / "claims.json"
    arquivo.write_text(json.dumps(CLAIMS), encoding="utf-8")
    assert importar.importar_checagens(banco, importar.checagens_de_arquivo(arquivo), "teste").inseridos == 2
    base = base_de(banco)
    assert [c.veredito for c in base.checagens("três parques públicos")][0] == "Exagerado"
    comparador = ComparadorFixo({})
    item = TestClient(criar_app(comparador, buscador_com(), base)).post("/analisar", json=pedido(buscar_na_web=False)).json()["afirmacoes"][0]
    assert item["resultado"] == "NOT_ENOUGH_EVIDENCE"
    assert item["checagens_anteriores"][0] == {
        "agencia": "Agência X", "data": "2026-03-15", "url": "https://agencia-x.org/c1",
        "alegacao_checada": "A cidade abriu três parques públicos em março.", "veredito": "Exagerado",
    }
    assert comparador.fontes_recebidas == []


def test_importar_urls_usa_leitor_seguro_e_limita_quantidade(banco, tmp_path, monkeypatch):
    lidas = []

    async def leitor(url):
        lidas.append(url)
        if "falha" in url:
            raise ValueError("URL não é pública ou não pôde ser resolvida.")
        return Fonte(url=url, titulo="Parques", dominio="a.gov.br", texto=PARQUES, origem="busca_web", data_publicacao="2026-03-01")

    lista = tmp_path / "urls.txt"
    lista.write_text("# fontes do grupo\nhttps://a.gov.br/parques\nhttps://falha.local/x\nhttps://a.gov.br/parques\n", encoding="utf-8")
    resultado = importar.importar_urls(banco, lista, leitor)
    assert lidas == ["https://a.gov.br/parques", "https://falha.local/x"]
    assert resultado.inseridos == 1 and len(resultado.erros) == 1
    monkeypatch.setitem(importar.BASE, "max_urls_por_carga", 1)
    with pytest.raises(ValueError, match="limite por carga"):
        importar.importar_urls(banco, lista, leitor)


def test_linha_de_comando_importa_textos_e_mostra_estatisticas(banco, tmp_path, capsys):
    arquivo = escrever(tmp_path, "Parques", PARQUES)
    assert main(["--base", str(banco), "importar-textos", str(arquivo), "--data", "2026-03-01"]) == 0
    assert "Inseridos: 1" in capsys.readouterr().out
    assert main(["--base", str(banco), "estatisticas"]) == 0
    estatisticas = json.loads(capsys.readouterr().out)
    assert estatisticas["documentos"] == 1 and estatisticas["documentos_sem_data"] == 0 and estatisticas["snapshot"].startswith("base-")


def test_reescrita_ou_texto_curto_alterado_nao_e_agrupado():
    # Limitação registrada: só cópias quase literais são agrupadas.
    original = Fonte(url="https://a.com.br/1", titulo="A", dominio="a.com.br", texto=PARQUES, origem="busca_web")
    alterado = Fonte(url="https://b.com.br/1", titulo="B", dominio="b.com.br", texto=PARQUES.replace("três parques", "3 parques"), origem="busca_web")
    agrupar([original, alterado])
    assert original.grupo != alterado.grupo


def test_mesmo_texto_em_publicacoes_diferentes_fica_na_base_com_cada_data(banco, tmp_path):
    from datetime import date

    async def leitor(url):
        data = "2026-03-20" if "tarde" in url else "2026-03-01"
        return Fonte(url=url, titulo=url, dominio=url.split("/")[2], texto=MATERIA, origem="busca_web", data_publicacao=data)

    lista = tmp_path / "urls.txt"
    lista.write_text("https://tarde.com.br/m\nhttps://cedo.com.br/m\n", encoding="utf-8")
    assert importar.importar_urls(banco, lista, leitor).inseridos == 2
    importar.indexar(banco, "embedding-teste", vetorizar)
    estatisticas = importar.estatisticas(banco)
    assert estatisticas["documentos"] == 2 and estatisticas["grupos_de_copias"] == 1
    # A publicação anterior continua disponível para uma notícia entre as duas datas.
    fontes = base_de(banco).buscar("três parques públicos", date(2026, 3, 10))
    assert {f.url for f in fontes} == {"https://cedo.com.br/m"}
    # Reimportar a mesma URL com o mesmo texto não duplica.
    assert importar.importar_urls(banco, lista, leitor).inseridos == 0


def test_arquivos_com_mesmo_nome_em_pastas_diferentes_sao_fontes_distintas(banco, tmp_path, capsys):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = escrever(tmp_path / "a", "relatorio", PARQUES)
    b = escrever(tmp_path / "b", "relatorio", MATERIA.replace("três parques", "dois parques").replace("zona norte", "zona leste"))
    importar.importar_textos(banco, [a, b])
    importar.indexar(banco, "embedding-teste", vetorizar)
    assert importar.estatisticas(banco)["dominios"] == 2
    # No relatório, cada documento sem URL aparece com um número que leva à sua origem.
    comparador = ComparadorFixo({f"documento:{a.resolve()}": ("entailment", 0.8, 0.9), f"documento:{b.resolve()}": ("entailment", 0.8, 0.9)})
    item = TestClient(criar_app(comparador, buscador_com(), base_de(banco))).post("/analisar", json=pedido(buscar_na_web=False)).json()["afirmacoes"][0]
    titulos = {e["fonte"] for e in item["evidencias"]}
    assert titulos == {"relatorio (base própria, documento 1)", "relatorio (base própria, documento 2)"}
    assert item["fontes_independentes"] == 2
    assert all(str(tmp_path) not in e["fonte"] for e in item["evidencias"])
    assert main(["--base", str(banco), "documento", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["referencia"] == str(b.resolve())
    assert main(["--base", str(banco), "documento", "99"]) == 1


def test_analise_le_um_snapshot_fixo_durante_carga_concorrente(banco, tmp_path):
    primeira = carregar(banco, tmp_path, {"Parques": PARQUES})
    base = base_de(banco)
    with base.leitura() as leitura:
        assert leitura.snapshot == primeira.carga
        segunda = carregar(banco, tmp_path, {"Vacina": VACINA})
        # Dentro da leitura, a carga nova não aparece: snapshot e busca continuam no estado anterior.
        assert leitura.snapshot == primeira.carga
        assert {f.titulo for f in leitura.buscar("campanha de vacinação contra a gripe")} == {"Parques (base própria, documento 1)"}
    assert base.snapshot == segunda.carga
    assert "Vacina (base própria, documento 2)" in {f.titulo for f in base.buscar("campanha de vacinação contra a gripe")}


def test_esquema_antigo_e_recusado(banco):
    import sqlite3

    sqlite3.connect(banco).execute("CREATE TABLE documentos (id INTEGER)").connection.close()
    with pytest.raises(RuntimeError, match="Recrie a base"):
        importar.estatisticas(banco)


def test_linha_de_comando_sinaliza_carga_parcial(banco, tmp_path, monkeypatch, capsys):
    async def leitor(url):
        if "falha" in url:
            raise ValueError("falhou")
        return Fonte(url=url, titulo="P", dominio="a.gov.br", texto=PARQUES, origem="busca_web")

    monkeypatch.setattr(importar, "ler_pagina", leitor)
    parcial = tmp_path / "parcial.txt"
    parcial.write_text("https://a.gov.br/p\nhttps://falha.local/x\n", encoding="utf-8")
    assert main(["--base", str(banco), "importar-urls", str(parcial)]) == 2
    so_falhas = tmp_path / "falhas.txt"
    so_falhas.write_text("https://falha.local/y\n", encoding="utf-8")
    assert main(["--base", str(banco), "importar-urls", str(so_falhas)]) == 1
    assert "erros: 1" in capsys.readouterr().out
