"""Gera os dados da demonstração técnica da VeritAI em demo/saida/demo.json.

Uso (precisa do extra "modelos"; na primeira vez baixa os modelos de v0 e v1):
    .venv/bin/python demo/gerar_demo.py

Conteúdo:
- casos: três afirmações fictícias (Exemplópolis), com evidências fornecidas e buscar_na_web=false,
  passadas pela API em memória com os perfis v0 e v1. Para cada passada: trechos e janelas de cada fonte,
  similaridades, janelas levadas ao NLI e probabilidades, limiares e regra de agregação, grupos de fontes
  independentes, relatório exato da API e tempo de cada etapa. Resultado esperado × obtido, sem ajuste.
- avaliacao: eval/avaliar.py no conjunto de contraste (só sanidade, não mede desempenho).
- testes: execução da suíte, por arquivo e por área.
- metadados: data, commit, versões.

O rastro é interno (parâmetro rastro do ComparadorNLI); a saída da API não muda.
"""

import importlib.util
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
# A demonstração nunca usa a base própria: sem o arquivo, o serviço usa só as evidências do pedido.
os.environ["VERITAI_BASE"] = str(Path(tempfile.gettempdir()) / "veritai-demo-sem-base" / "nao-existe.sqlite3")

from fastapi.testclient import TestClient  # noqa: E402

from veritai.api import criar_app  # noqa: E402
from veritai.config import CONFIG, LIMIARES, SELECAO, versoes  # noqa: E402
from veritai.modelos import ComparadorNLI  # noqa: E402
from veritai.regras import agregar, explicar  # noqa: E402

SAIDA = RAIZ / "demo" / "saida" / "demo.json"
PERFIS = ["v0", "v1"]
DATA_NOTICIA = "2026-03-20"

# Textos fictícios: Exemplópolis e seus órgãos não existem; domínios .example são reservados para exemplos.
DIARIO_PARQUES = (
    "A Secretaria de Parques de Exemplópolis inaugurou três parques públicos na zona norte da cidade no início de março. "
    "Os novos espaços ficam nos bairros Jardim das Acácias, Vila Serena e Alto do Moinho. "
    "Cada parque tem pista de caminhada, quadra poliesportiva, parquinho infantil e uma área cercada para cães. "
    "Segundo a secretaria, as obras levaram oito meses e foram feitas com recursos do orçamento municipal. "
    "Os parques vão funcionar todos os dias, das seis da manhã às dez da noite, com rondas da guarda municipal. "
    "Moradores ouvidos pela reportagem elogiaram as pistas, mas pediram mais bebedouros e banheiros públicos. "
    "A secretaria informou que outros dois parques estão em obras na zona sul e devem ser entregues no segundo semestre."
)
CASOS = [
    {
        "id": "caso-1-apoiada",
        "esperado": "SUPPORTED",
        "por_que": (
            "Duas fontes independentes (o Diário e a nota da Secretaria) afirmam que três parques foram inaugurados na zona "
            "norte em março; o Portal Vizinhança reproduz o texto do Diário e deve contar como a mesma fonte."
        ),
        "pedido": {
            "noticia": {"titulo": "Exemplópolis ganha três parques na zona norte", "data": DATA_NOTICIA},
            "afirmacoes": ["A Secretaria de Parques de Exemplópolis inaugurou três parques públicos na zona norte em março."],
            "evidencias": [
                {"titulo": "Diário de Exemplópolis", "url": "https://diario.exemplopolis.example/2026/03/parques", "origem": "link_publisher",
                 "data_publicacao": "2026-03-05", "texto": DIARIO_PARQUES},
                {"titulo": "Portal Vizinhança (reprodução)", "url": "https://portal-vizinhanca.example/noticias/parques", "origem": "link_publisher",
                 "data_publicacao": "2026-03-06", "texto": DIARIO_PARQUES.replace("no início de março", "nos primeiros dias de março")},
                {"titulo": "Nota da Secretaria de Parques de Exemplópolis", "origem": "anexo", "data_publicacao": "2026-03-04",
                 "texto": (
                     "Nota oficial. A Secretaria de Parques de Exemplópolis entregou nesta semana três novos parques públicos, "
                     "todos na zona norte do município. A inauguração ocorreu em março, com a presença de moradores. "
                     "A secretaria agradece às associações de bairro pela parceria."
                 )},
            ],
            "buscar_na_web": False,
        },
    },
    {
        "id": "caso-2-contradita",
        "esperado": "REFUTED",
        "por_que": (
            "A nota da Secretaria de Obras e a Rádio Exemplópolis dizem, cada uma, que a ponte não foi interditada e que o "
            "tráfego segue liberado, o oposto da afirmação."
        ),
        "pedido": {
            "noticia": {"titulo": "Reforma da ponte da Avenida Central", "data": DATA_NOTICIA},
            "afirmacoes": ["A ponte da Avenida Central de Exemplópolis foi interditada durante a obra de reforma."],
            "evidencias": [
                {"titulo": "Nota da Secretaria de Obras de Exemplópolis", "origem": "anexo", "data_publicacao": "2026-03-12",
                 "texto": (
                     "A Secretaria de Obras de Exemplópolis informa que a ponte da Avenida Central não será interditada durante a "
                     "obra de reforma. O trabalho será feito por etapas, sempre com as duas faixas liberadas para veículos. "
                     "A previsão de conclusão é o fim de maio."
                 )},
                {"titulo": "Rádio Exemplópolis", "url": "https://radio.exemplopolis.example/transito/ponte", "origem": "link_publisher",
                 "data_publicacao": "2026-03-18",
                 "texto": (
                     "Ao contrário do que circulou nas redes sociais, a ponte da Avenida Central não foi interditada. "
                     "A reportagem esteve no local nesta manhã e o tráfego seguia normal nas duas faixas, com operários "
                     "trabalhando apenas na calçada lateral."
                 )},
            ],
            "buscar_na_web": False,
        },
    },
    {
        "id": "caso-3-sem-evidencia",
        "esperado": "NOT_ENOUGH_EVIDENCE",
        "por_que": (
            "As fontes tratam da reforma da ponte (etapas, prazo e trânsito), mas nenhuma informa quanto a obra custou; "
            "a nota diz que o valor do contrato não foi divulgado."
        ),
        "pedido": {
            "noticia": {"titulo": "Custo da reforma da ponte", "data": DATA_NOTICIA},
            "afirmacoes": ["A reforma da ponte da Avenida Central de Exemplópolis custou 12 milhões de reais."],
            "evidencias": [
                {"titulo": "Nota da Secretaria de Obras de Exemplópolis", "origem": "anexo", "data_publicacao": "2026-03-12",
                 "texto": (
                     "A reforma da ponte da Avenida Central começou em março e será feita por etapas, com as duas faixas "
                     "liberadas. A previsão de conclusão é o fim de maio. O valor do contrato ainda não foi divulgado pela prefeitura."
                 )},
                {"titulo": "Rádio Exemplópolis", "url": "https://radio.exemplopolis.example/transito/ponte-etapas", "origem": "link_publisher",
                 "data_publicacao": "2026-03-18",
                 "texto": (
                     "Motoristas que passam pela ponte da Avenida Central devem reduzir a velocidade perto da calçada lateral, onde "
                     "operários trabalham desde o início do mês. A empresa responsável não quis comentar o andamento da obra."
                 )},
            ],
            "buscar_na_web": False,
        },
    },
]

# Área de cada teste: a primeira área cuja palavra-chave aparece no nome do teste (em minúsculas).
# Testes que não casam com nenhuma área ficam em "Outros (contrato, regras e API)".
AREAS = [
    ("Demonstração (rastro e explicação)", ["rastro", "explicar", "demo"]),
    ("Segurança de URLs e leitura de páginas", ["url", "dns", "ipv6", "ipv4", "host", "sni", "redirecion", "idna", "leitura_de_pagina",
                                                 "content_length", "bloco", "compress", "nao_publico", "falhas_parciais"]),
    ("Base própria (SQLite, busca, carga)", ["base", "bm25", "rrf", "trechos_de_150", "snapshot", "importar", "linha_de_comando",
                                             "esquema", "consulta_fts", "documento_novo", "carga", "perfil_errado", "publicacoes"]),
    ("Cópias e fontes independentes", ["copia", "agrupa", "dominio", "independente", "reescrita", "mesmo_nome"]),
    ("Data da notícia", ["data"]),
    ("Checagens anteriores", ["checag"]),
    ("Perfis, V1 e seleção de janelas", ["perfil", "janela", "selec", "consolidar", "rotulos_nli", "prefixo", "comparador", "candidatos",
                                         "dividir", "limiares", "texto_do_publisher"]),
    ("Avaliação (eval)", ["metricas", "bootstrap", "contraste", "eval", "carregar_rejeita"]),
]
OUTROS = "Outros (contrato, regras e API)"


class ComparadorComRastro:
    """Repassa ao ComparadorNLI e guarda o rastro de cada chamada; a API recebe os mesmos itens."""

    def __init__(self, comparador) -> None:
        self.comparador = comparador
        self.perfil = comparador.perfil
        self.chamadas: list[dict] = []

    def comparar(self, afirmacao, fontes):
        rastro: dict = {}
        itens = self.comparador.comparar(afirmacao, fontes, rastro=rastro)
        self.chamadas.append({"afirmacao": afirmacao, "itens": itens, "rastro": rastro})
        return itens


async def sem_busca(_afirmacao):
    raise RuntimeError("A demonstração não usa a busca na web.")


def arredondar(valor, casas: int = 6):
    if isinstance(valor, float):
        return round(valor, casas)
    if isinstance(valor, dict):
        return {chave: arredondar(item, casas) for chave, item in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [arredondar(item, casas) for item in valor]
    return valor


def grupos_do_rastro(rastro: dict) -> list[dict]:
    grupos: dict[str, list[str]] = {}
    for fonte in rastro["fontes"]:
        grupos.setdefault(fonte["grupo"], []).append(fonte["titulo"])
    return [{"grupo": grupo, "fontes": titulos} for grupo, titulos in grupos.items()]


def rodar_caso(caso: dict, comparador) -> dict:
    """Uma passada de um caso por um perfil, pela API em memória."""
    envoltorio = ComparadorComRastro(comparador)
    client = TestClient(criar_app(envoltorio, sem_busca, None))
    inicio = time.perf_counter()
    resposta = client.post("/analisar", json=caso["pedido"])
    requisicao = time.perf_counter() - inicio
    resposta.raise_for_status()
    relatorio = resposta.json()
    chamada = envoltorio.chamadas[0]
    # Etapas de uma única requisição: as do comparador (medidas no rastro) e o restante, que inclui
    # validação do pedido, agrupamento de fontes, regras de agregação e montagem do relatório.
    tempos = dict(chamada["rastro"]["tempos_s"])
    tempos["regras_agrupamento_e_montagem_do_relatorio"] = requisicao - sum(chamada["rastro"]["tempos_s"].values())
    tempos["requisicao_total"] = requisicao
    # Medição separada, fora da requisição: o serviço não cronometra as regras, então elas são reexecutadas
    # isoladamente só para mostrar a ordem de grandeza. Não soma com os tempos acima.
    inicio = time.perf_counter()
    agregar(chamada["itens"])
    regras_isoladas = time.perf_counter() - inicio
    obtido = relatorio["afirmacoes"][0]["resultado"]
    return {
        "perfil": comparador.perfil,
        "resultado_obtido": obtido,
        "acertou": obtido == caso["esperado"],
        "rastro": chamada["rastro"],
        "grupos_de_fontes_independentes": grupos_do_rastro(chamada["rastro"]),
        "agregacao": explicar(chamada["itens"]),
        "relatorio_api": relatorio,
        "tempos_s": tempos,
        "tempo_isolado_das_regras_s": regras_isoladas,
    }


def carregar_avaliar():
    especificacao = importlib.util.spec_from_file_location("avaliar", RAIZ / "eval" / "avaliar.py")
    modulo = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(modulo)
    return modulo


def rodar_avaliacao(comparadores: dict, cargas: dict) -> dict:
    avaliar = carregar_avaliar()
    itens = avaliar.carregar(RAIZ / "eval" / "contraste.jsonl")
    por_perfil = {}
    for perfil, comparador in comparadores.items():
        inicio = time.perf_counter()
        predicoes = avaliar.prever(itens, comparador)
        inferencia = time.perf_counter() - inicio
        resultado = avaliar.metricas([p["rotulo"] for p in predicoes], [p["predito"] for p in predicoes])
        por_perfil[perfil] = {
            **resultado,
            "tempos_s": {"carga_dos_modelos": cargas[perfil], "inferencia": inferencia},
            "predicoes": [{**p, "acertou": p["rotulo"] == p["predito"]} for p in predicoes],
        }
    return {
        "aviso": "Conjunto de contraste: 28 itens sintéticos escritos à mão. Só teste de sanidade; não mede desempenho e não pode ser usado em treino ou calibração.",
        "arquivo": "eval/contraste.jsonl",
        "bootstrap": {"repeticoes": 1000, "semente": 0, "intervalo": "95%, percentis 2,5 e 97,5"},
        "perfis": por_perfil,
    }


def area_do_teste(nome: str) -> str:
    nome = nome.lower()
    for area, palavras in AREAS:
        if any(palavra in nome for palavra in palavras):
            return area
    return OUTROS


def resumir_junit(caminho: Path) -> dict:
    casos = ElementTree.parse(caminho).getroot().iter("testcase")
    por_arquivo: dict[str, dict[str, int]] = {}
    por_area: dict[str, dict[str, int]] = {}
    totais = {"passou": 0, "falhou": 0, "pulado": 0}
    falhas = []
    for caso in casos:
        arquivo = caso.get("file") or caso.get("classname", "").replace(".", "/") + ".py"
        if caso.find("failure") is not None or caso.find("error") is not None:
            situacao = "falhou"
            falhas.append(f"{arquivo}::{caso.get('name')}")
        elif caso.find("skipped") is not None:
            situacao = "pulado"
        else:
            situacao = "passou"
        totais[situacao] += 1
        for tabela, chave in ((por_arquivo, arquivo), (por_area, area_do_teste(caso.get("name", "")))):
            tabela.setdefault(chave, {"passou": 0, "falhou": 0, "pulado": 0})[situacao] += 1
    return {"totais": {**totais, "total": sum(totais.values())}, "por_arquivo": por_arquivo, "por_area": por_area, "falhas": falhas}


def rodar_testes() -> dict:
    with tempfile.TemporaryDirectory() as pasta:
        junit = Path(pasta) / "junit.xml"
        inicio = time.perf_counter()
        processo = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
            cwd=RAIZ, capture_output=True, text=True,
        )
        duracao = time.perf_counter() - inicio
        resumo = resumir_junit(junit)
    return {
        "comando": "python -m pytest -q -p no:cacheprovider",
        "codigo_de_saida": processo.returncode,
        "linha_final": processo.stdout.strip().splitlines()[-1] if processo.stdout.strip() else "",
        "duracao_s": duracao,
        "regra_das_areas": "Primeira área cuja palavra-chave aparece no nome do teste; sem correspondência, " + OUTROS + ".",
        "palavras_por_area": dict(AREAS),
        **resumo,
    }


def git(*argumentos: str) -> str:
    return subprocess.run(["git", *argumentos], cwd=RAIZ, capture_output=True, text=True).stdout.strip()


def metadados() -> dict:
    import torch
    import transformers

    alterados = git("status", "--porcelain")
    return {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": git("rev-parse", "HEAD"),
        "commit_resumo": git("log", "-1", "--format=%h %s"),
        "alteracoes_locais": bool(alterados),
        "arquivos_alterados": alterados.splitlines(),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "plataforma": f"{platform.system()} {platform.machine()}",
        "versao_config": CONFIG["versao_config"],
        "calibrado": CONFIG["calibrado"],
        "versoes_por_perfil": {perfil: versoes(perfil) for perfil in PERFIS},
        "limiares": {chave: valor for chave, valor in LIMIARES.items() if not chave.startswith("_")},
        "selecao": {chave: valor for chave, valor in SELECAO.items() if not chave.startswith("_")},
    }


def main() -> None:
    comparadores, cargas = {}, {}
    for perfil in PERFIS:
        comparador = ComparadorNLI(perfil)
        inicio = time.perf_counter()
        comparador.modelos._carregar()
        cargas[perfil] = time.perf_counter() - inicio
        comparadores[perfil] = comparador
        print(f"Modelos de {perfil} carregados em {cargas[perfil]:.1f} s.")

    casos = []
    for caso in CASOS:
        passadas = {perfil: rodar_caso(caso, comparador) for perfil, comparador in comparadores.items()}
        casos.append({
            "id": caso["id"],
            "afirmacao": caso["pedido"]["afirmacoes"][0],
            "esperado": caso["esperado"],
            "por_que_esperado": caso["por_que"],
            "pedido": caso["pedido"],
            "passadas": passadas,
        })
        print(f"{caso['id']}: esperado {caso['esperado']}; " + ", ".join(f"{p} {v['resultado_obtido']}" for p, v in passadas.items()))

    avaliacao = rodar_avaliacao(comparadores, cargas)
    print("Avaliação no contraste: " + ", ".join(f"{p} macro-F1 {v['macro_f1']:.3f}" for p, v in avaliacao["perfis"].items()))
    testes = rodar_testes()
    print(f"Testes: {testes['linha_final']}")

    demo = {
        "descricao": "Dados da demonstração técnica da VeritAI (sem frontend). Casos fictícios; avaliação de contraste é só sanidade.",
        "metadados": metadados(),
        "casos": casos,
        "avaliacao_contraste": avaliacao,
        "testes": testes,
    }
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(arredondar(demo), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Gravado em {SAIDA.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
