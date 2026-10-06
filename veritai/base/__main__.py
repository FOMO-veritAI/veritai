"""Linha de comando da base própria: python -m veritai.base <comando> ...

Não use para coleta em massa nem raspagem: as URLs vêm de uma lista curada pelo
grupo. Nunca carregue as notícias fictícias da interface do FOMO.
"""

import argparse
import json
import sys
from pathlib import Path

from ..config import caminho_base, perfil
from . import importar


def main(argumentos: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m veritai.base", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", type=Path, default=None, help="caminho do banco (padrão: VERITAI_BASE ou data/veritai.sqlite3)")
    comandos = parser.add_subparsers(dest="comando", required=True)

    urls = comandos.add_parser("importar-urls", help="lê as páginas de um arquivo com uma URL por linha")
    urls.add_argument("arquivo", type=Path)

    textos = comandos.add_parser("importar-textos", help="importa arquivos de texto locais (.txt, .md)")
    textos.add_argument("arquivos", type=Path, nargs="+")
    textos.add_argument("--data", help="data de publicação dos documentos (AAAA-MM-DD)")

    checagens = comandos.add_parser("importar-checagens", help="importa ClaimReview do Google Fact Check")
    origem = checagens.add_mutually_exclusive_group(required=True)
    origem.add_argument("--arquivo", type=Path, help="JSON no formato da resposta de claims:search")
    origem.add_argument("--consulta", help="uma consulta à API (exige GOOGLE_FACT_CHECK_API_KEY)")

    indexar = comandos.add_parser("indexar", help="calcula os vetores de um perfil para os trechos sem vetor")
    indexar.add_argument("--perfil", required=True)

    comandos.add_parser("estatisticas", help="mostra o conteúdo da base")

    mostrar = comandos.add_parser("documento", help="mostra a origem de um documento (o número aparece no título da evidência)")
    mostrar.add_argument("numero", type=int)

    args = parser.parse_args(argumentos)
    caminho = args.base or caminho_base()

    if args.comando == "importar-urls":
        resultado = importar.importar_urls(caminho, args.arquivo)
    elif args.comando == "importar-textos":
        resultado = importar.importar_textos(caminho, args.arquivos, args.data)
    elif args.comando == "importar-checagens":
        if args.arquivo:
            lidas, comando = importar.checagens_de_arquivo(args.arquivo), f"importar-checagens {args.arquivo.name}"
        else:
            lidas, comando = importar.checagens_de_consulta(args.consulta), "importar-checagens --consulta"
        resultado = importar.importar_checagens(caminho, lidas, comando)
    elif args.comando == "indexar":
        from ..modelos import ModelosHF

        embedding = perfil(args.perfil)["embedding"]
        total = importar.indexar(caminho, embedding, ModelosHF(args.perfil).vetorizar)
        print(f"{total} trecho(s) indexado(s) com {embedding}.")
        return 0
    elif args.comando == "documento":
        dados = importar.documento(caminho, args.numero)
        if dados is None:
            print(f"Documento {args.numero} não encontrado.", file=sys.stderr)
            return 1
        print(json.dumps(dados, ensure_ascii=False, indent=2))
        return 0
    else:
        print(json.dumps(importar.estatisticas(caminho), ensure_ascii=False, indent=2))
        return 0

    print(f"Inseridos: {resultado.inseridos}; já existentes: {resultado.repetidos}; erros: {len(resultado.erros)}; snapshot: {resultado.carga or 'sem mudança'}.")
    for erro in resultado.erros:
        print(f"Erro: {erro}", file=sys.stderr)
    # 0: tudo certo; 1: nada entrou por erro; 2: carga parcial (algo entrou, mas houve erros).
    if not resultado.erros:
        return 0
    return 2 if resultado.inseridos else 1


if __name__ == "__main__":
    sys.exit(main())
