"""Gera o site de embeddings da VeritAI sobre o TensorFlow Embedding Projector.

    python projetor/gerar.py --saida _site [--pares 1500] [--tensorboard logs/projetor]

Os vetores são os mesmos que o serviço calcula (ModelosHF.vetorizar, com os prefixos de cada perfil):
afirmações como "consulta" e trechos como "trecho". Os textos são pares do ASSIN 2 (treino) e o
conjunto de contraste. O app é o Embedding Projector do TensorFlow (Apache 2.0), numa versão fixa,
sem o Google Analytics da página de demonstração.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

import numpy

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from veritai.modelos import ModelosHF  # noqa: E402

AQUI = Path(__file__).resolve().parent
COMMIT_PROJECTOR = "6ff71f7233ac5947450409e7938b58ec14d1e24c"
URL_PROJECTOR = f"https://raw.githubusercontent.com/tensorflow/embedding-projector-standalone/{COMMIT_PROJECTOR}/index.html"
SHA256_PROJECTOR = "9260f30236f17e7c268b87c30b2006d83c523b924a96f31fabdca6fa141166d8"
URL_ASSIN2 = "https://huggingface.co/datasets/nilc-nlp/assin2/resolve/refs%2Fconvert%2Fparquet/default/train/0000.parquet"
PERFIS = {"v0": "VeritAI v0 · paraphrase-multilingual-MiniLM-L12-v2", "v1": "VeritAI v1 · multilingual-e5-base"}
COLUNAS = ["texto", "papel", "origem", "rotulo", "par"]

# Trocas no index.html do projector; cada uma precisa acontecer exatamente uma vez.
TROCAS = [
    (r"<script>\s*\(function\(i,s,o,g,r,a,m\).*?ga\('create', 'UA-46457317-5', 'auto'\);\s*</script>", ""),
    (r'projector-config-json-path="oss_data/oss_demo_projector_config.json" page-view-logging>', 'projector-config-json-path="dados/projector_config.json">'),
    (r"<title>Embedding projector - visualization of high-dimensional data</title>", "<title>Embeddings da VeritAI · Embedding Projector</title>"),
    (r'<link rel="icon" type="image/png" href="../favicon.png">', '<link rel="icon" type="image/svg+xml" href="../favicon.svg">'),
    (r'(\s*<meta (?:property="og|name="twitter):[^>]*>)+', ""),
]


def baixar_projector() -> str:
    with urllib.request.urlopen(URL_PROJECTOR, timeout=60) as resposta:
        conteudo = resposta.read()
    if hashlib.sha256(conteudo).hexdigest() != SHA256_PROJECTOR:
        raise SystemExit("O index.html do Embedding Projector não confere com o hash esperado.")
    html = conteudo.decode("utf-8")
    for padrao, troca in TROCAS:
        html, n = re.subn(padrao, troca, html, flags=re.DOTALL)
        if n != 1:
            raise SystemExit(f"Troca esperada uma vez, aconteceu {n}: {padrao[:60]}")
    if "google-analytics" in html:
        raise SystemExit("O Google Analytics continua na página.")
    return html


def carregar_assin2(pares: int, semente: int = 42):
    import pandas

    arquivo = RAIZ / "data" / "assin2" / "train.parquet"
    if not arquivo.exists():
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL_ASSIN2, arquivo)
    dados = pandas.read_parquet(arquivo)
    # Metade de cada rótulo, para as duas cores aparecerem na mesma proporção.
    amostra = dados.groupby("entailment_judgment", group_keys=False).sample(n=pares // 2, random_state=semente).sort_values("sentence_pair_id")
    linhas = []
    for par in amostra.itertuples():
        rotulo = "apoia (ENTAILMENT)" if par.entailment_judgment == 1 else "não apoia (NONE)"
        linhas.append({"texto": par.premise, "papel": "trecho", "origem": "ASSIN 2 (treino)", "rotulo": rotulo, "par": f"assin2-{par.sentence_pair_id}"})
        linhas.append({"texto": par.hypothesis, "papel": "afirmação", "origem": "ASSIN 2 (treino)", "rotulo": rotulo, "par": f"assin2-{par.sentence_pair_id}"})
    return linhas


def carregar_contraste():
    linhas = []
    for linha in (RAIZ / "eval" / "contraste.jsonl").read_text(encoding="utf-8").splitlines():
        if not linha.strip():
            continue
        item = json.loads(linha)
        linhas.append({"texto": item["afirmacao"], "papel": "afirmação", "origem": "contraste (sintético)", "rotulo": item["rotulo"], "par": item["id"]})
        for evidencia in item["evidencias"]:
            linhas.append({"texto": evidencia["texto"], "papel": "trecho", "origem": "contraste (sintético)", "rotulo": item["rotulo"], "par": item["id"]})
    return linhas


def limpar(valor: str) -> str:
    return re.sub(r"\s+", " ", str(valor)).strip()


def vetores(linhas, perfil: str) -> numpy.ndarray:
    modelos = ModelosHF(perfil)
    afirmacoes = [i for i, linha in enumerate(linhas) if linha["papel"] == "afirmação"]
    trechos = [i for i, linha in enumerate(linhas) if linha["papel"] == "trecho"]
    va = modelos.vetorizar([linhas[i]["texto"] for i in afirmacoes], "consulta")
    vt = modelos.vetorizar([linhas[i]["texto"] for i in trechos], "trecho")
    resultado = numpy.zeros((len(linhas), va.shape[1]), dtype=numpy.float32)
    resultado[afirmacoes], resultado[trechos] = va, vt
    return resultado


def gravar_metadados(caminho: Path, linhas) -> None:
    texto = "\t".join(COLUNAS) + "\n" + "".join("\t".join(limpar(linha[c]) for c in COLUNAS) + "\n" for linha in linhas)
    caminho.write_text(texto, encoding="utf-8")


def gravar_tensorboard(pasta: Path, linhas, tensores: dict[str, numpy.ndarray]) -> None:
    """Mesmo conteúdo para o plugin de projetor do TensorBoard: tensorboard --logdir <pasta>."""
    pasta.mkdir(parents=True, exist_ok=True)
    gravar_metadados(pasta / "metadados.tsv", linhas)
    blocos = []
    for perfil, matriz in tensores.items():
        numpy.savetxt(pasta / f"{perfil}.tsv", matriz, delimiter="\t", fmt="%.6f")
        blocos.append(f'embeddings {{\n  tensor_name: "{PERFIS[perfil]}"\n  tensor_path: "{perfil}.tsv"\n  metadata_path: "metadados.tsv"\n}}\n')
    (pasta / "projector_config.pbtxt").write_text("".join(blocos), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--saida", type=Path, required=True)
    parser.add_argument("--pares", type=int, default=1500, help="pares do ASSIN 2 (metade de cada rótulo)")
    parser.add_argument("--tensorboard", type=Path, help="pasta de logs para o TensorBoard")
    args = parser.parse_args(argv)

    linhas = carregar_assin2(args.pares) + carregar_contraste()
    tensores = {perfil: vetores(linhas, perfil) for perfil in PERFIS}

    app = args.saida / "app"
    dados = app / "dados"
    if args.saida.exists():
        shutil.rmtree(args.saida)
    dados.mkdir(parents=True)
    for arquivo in ("index.html", "favicon.svg"):
        shutil.copy(AQUI / "site" / arquivo, args.saida / arquivo)
    (app / "index.html").write_text(baixar_projector(), encoding="utf-8")
    gravar_metadados(dados / "metadados.tsv", linhas)
    config = {"embeddings": []}
    for perfil, matriz in tensores.items():
        (dados / f"{perfil}.bytes").write_bytes(matriz.astype("<f4").tobytes())
        config["embeddings"].append({
            "tensorName": PERFIS[perfil],
            "tensorShape": list(matriz.shape),
            "tensorPath": f"dados/{perfil}.bytes",
            "metadataPath": "dados/metadados.tsv",
        })
    (dados / "projector_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.tensorboard:
        gravar_tensorboard(args.tensorboard, linhas, tensores)
    print(f"{len(linhas)} textos; " + ", ".join(f"{p}: {m.shape[1]} dimensões" for p, m in tensores.items()) + f"; site em {args.saida}")


if __name__ == "__main__":
    main()
