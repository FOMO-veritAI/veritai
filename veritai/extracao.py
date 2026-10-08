"""Extração de afirmações candidatas por regras (abordagem A, sem LLM).

Divide o título e o texto da notícia em frases e propõe as que parecem fatos verificáveis:
com número, período, nome próprio, atribuição ou verbo de fato, e sem marcas de opinião.
O publicador confirma, edita ou descarta cada candidata antes da análise; nada aqui decide
resultado.

O que esta abordagem não faz (fica para uma abordagem com LLM e travas):
- descontextualizar: "A taxa caiu para 5,3%" continua sem dizer qual taxa. Só as datas são
  completadas pela data da notícia: meses sem ano ganham o ano, como na entrada do NLI, e
  "nesta terça-feira (6)" ganha mês e ano, conferido o dia da semana;
- separar fatos ligados por "e" na mesma oração; só partes separadas por ponto e vírgula
  viram candidatas próprias.
Os alertas dizem ao publicador o que conferir em cada candidata.

Regras de operação, não validadas em dados anotados.
"""

import re
from dataclasses import dataclass, field

from datetime import date, timedelta

from .config import CONFIG
from .relatorio import AfirmacaoCandidata, PedidoExtracao, RelatorioExtracao
from .verificador import MESES, anotar_ano, nomes, periodos, quantidades, sem_acentos

VERSAO = "regras-2026-10-08"
MAXIMO_DE_AFIRMACOES = 10  # o mesmo limite do /analisar
CARACTERES_MINIMOS, CARACTERES_MAXIMOS = 10, 500  # limites de cada afirmação no /analisar
PALAVRAS_MINIMAS, PALAVRAS_MAXIMAS = 6, 70

# Abreviações que terminam em ponto sem fim de frase (comparadas em minúsculas).
ABREVIACOES = {"sr", "sra", "srs", "dr", "dra", "prof", "profa", "exmo", "gov", "dep", "sen", "min", "art", "arts", "inc", "n", "nº",
               "p.p", "etc", "aprox", "ref", "cap", "vol", "pág", "tel", "av", "r"}
FIM_DE_FRASE = re.compile(r"[.!?…][\"”’)]?\s+(?=[\"“‘(]?[A-ZÀ-Ý0-9])")
# Linhas que são navegação, crédito ou rodapé de página, não texto da notícia.
ROTINA = re.compile(
    r"^(?:compartilhe|publicado em|atualizado em|foto|fotos|imagem|crédito|créditos|edição|reportagem|leia (?:mais|também)|"
    r"siga|clique|assista|veja (?:também|mais)|saiba mais|fonte|colaboradores|assessoria de comunicação)\b",
    re.IGNORECASE,
)
# "Segundo" só é atribuição antes de artigo, nome ou "dados"/"levantamento": "segundo maior saldo" é ordinal.
SEGUNDO = r"\b[Ss]egundo\s+(?-i:(?=[oa]s?\s|[A-ZÀ-Ý]|dados\b|levantamento\b|pesquisa\b|estimativas?\b))"
ATRIBUICAO = re.compile(
    SEGUNDO + r"|\b(?:de acordo com|conforme|informou|informa|afirmou|afirma|disse|diz|declarou|divulgou|divulga|anunciou|"
    r"aponta|apontam|mostra|mostram|revela|revelam|estima|estimam|indica|indicam)\b",
    re.IGNORECASE,
)
FONTE_ATRIBUIDA = re.compile(
    r"(?:" + SEGUNDO + r"|\b[Dd]e acordo com\s+|\b[Cc]onforme\s+)(?:[oa]s?\s+)?((?:[A-ZÀ-Ý]|dados\b|levantamento\b|pesquisa\b|estimativas?\b)[^,;.]{1,59})"
)
VERBO_DE_FATO = re.compile(
    r"\b(?:cresc\w*|ca[ií]\w*|caem|recu\w*|sub\w*|aument\w*|diminu\w*|reduz\w*|registr\w*|atingi\w*|alcan[çc]\w*|aprov\w*|"
    r"sancion\w*|public\w*|lan[çc]\w*|inaugur\w*|invest\w*|destin\w*|liber\w*|arrecad\w*|morre\w*|vari\w*|"
    r"foi|foram|é de|são de|ficou|ficaram|soma\w*|totaliz\w*|chegou|chegaram|reuniu|reúne)\b",
    re.IGNORECASE,
)
# Sobras de página no meio do texto colado (rodapé, "Relacionadas", créditos de edição).
LIXO_DE_PAGINA = re.compile(r"compartilhe essa notícia|continuar lendo|\bEdição: |\bRelacionadas\b|\bLeia também\b|Versão em áudio", re.IGNORECASE)
DIAS_DA_SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
DIA_RELATIVO = re.compile(r"\b(segunda|terça|quarta|quinta|sexta)(?:-feira)?\s*\((\d{1,2})\)|\b(sábado|domingo)\s*\((\d{1,2})\)", re.IGNORECASE)
OPINIAO = re.compile(
    r"\b(?:eu|meu|minha|nós|nosso|nossa|acho|acredito|absurd\w*|vergonh\w*|lament[áa]vel|incr[ií]vel|p[eé]ssim\w*|rid[ií]cul\w*|"
    r"excelente|maravilh\w*|lixo|canalha|bandid\w*)\b",
    re.IGNORECASE,
)
FUTURO = re.compile(r"\b(?:vai|v[ãa]o|ser[áã]o?|ir[áã]o?|dever[áã]o?|deve|devem|pretende\w*|planej\w*|poder[áã]o?|promete\w*|espera-se|previst\w*)\b", re.IGNORECASE)
# Começos que dependem da frase anterior ("Com isso", "Ele", "O índice").
ANAFORA = re.compile(
    r"^(?:ele|ela|eles|elas|isso|isto|esse|essa|este|esta|com isso|além disso|já|também|o índice|a taxa|o indicador|a pesquisa|o resultado)\b",
    re.IGNORECASE,
)
MESES_OU_DATA = re.compile(
    r"(?<!rio de )\b(?:janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro|trimestre|semestre|"
    r"(?:19|20)\d{2}|\d{1,2}/\d{1,2})\b",
    re.IGNORECASE,
)


@dataclass
class Candidata:
    texto: str  # afirmação proposta: a frase, com o ano nos meses citados sem ano
    trecho_origem: str  # frase literal da notícia
    posicao: int  # 0 = título; depois, a ordem da frase no texto
    pontuacao: int
    sinais: list[str] = field(default_factory=list)  # por que foi proposta
    alertas: list[str] = field(default_factory=list)  # o que o publicador deve conferir
    atribuicao: str | None = None  # quem afirma, quando a frase diz ("segundo o IBGE")


def frases(texto: str) -> list[str]:
    """Frases do texto: quebra por parágrafo e por fim de frase, sem partir abreviações ("p.p.", "Dr.")."""
    resultado = []
    for paragrafo in re.split(r"\n", texto):
        paragrafo = re.sub(r"\s+", " ", paragrafo).strip()
        inicio = 0
        for achado in FIM_DE_FRASE.finditer(paragrafo):
            palavra = re.search(r"([\w.º]+)[.!?…][\"”’)]?$", paragrafo[inicio : achado.end()].rstrip())
            anterior = palavra.group(1).lower() if palavra else ""
            # Abreviação ou inicial de nome ("Robert E. Lee") não encerram a frase.
            if paragrafo[achado.start()] == "." and (anterior in ABREVIACOES or (len(anterior) == 1 and anterior.isalpha())):
                continue
            resultado.append(paragrafo[inicio : achado.end()].strip())
            inicio = achado.end()
        if paragrafo[inicio:].strip():
            resultado.append(paragrafo[inicio:].strip())
    return resultado


def avaliar(frase: str) -> tuple[int, list[str], list[str], str | None]:
    """(pontuação, sinais, alertas, motivo de descarte) de uma frase."""
    palavras = len(frase.split())
    if ROTINA.match(frase) or LIXO_DE_PAGINA.search(frase):
        return 0, [], [], "rotina de página"
    if not PALAVRAS_MINIMAS <= palavras <= PALAVRAS_MAXIMAS:
        return 0, [], [], "tamanho"
    if frase.rstrip().endswith("?"):
        return 0, [], [], "pergunta"
    if "!" in frase or OPINIAO.search(frase):
        return 0, [], [], "opinião"
    sinais, alertas, pontuacao = [], [], 0
    if quantidades(frase):
        sinais.append("número")
        pontuacao += 2
    if not periodos(frase).vazio or MESES_OU_DATA.search(frase):
        sinais.append("período")
        pontuacao += 1
    if nomes(frase):
        sinais.append("nome próprio")
        pontuacao += 1
    if ATRIBUICAO.search(frase):
        sinais.append("atribuição")
        pontuacao += 1
    if VERBO_DE_FATO.search(frase):
        sinais.append("verbo de fato")
        pontuacao += 1
    if FUTURO.search(frase):
        alertas.append("futuro ou intenção: só é verificável se for agenda oficial")
        pontuacao -= 1
    if re.search(r"[\"“”]", frase):
        alertas.append("citação: decidir se verifica a fala ou o fato")
    if ANAFORA.match(frase) or frase[:1].islower():
        alertas.append("depende da frase anterior: completar o sujeito")
    if "período" not in sinais:
        alertas.append("sem período: o verificador não confere a data")
    # Precisa de algo concreto para comparar com uma fonte: número, nome ou data.
    if not {"número", "nome próprio", "período"} & set(sinais):
        return pontuacao, sinais, alertas, "sem número, nome nem data"
    return pontuacao, sinais, alertas, None


def datar(frase: str, data: str | None) -> str:
    """Completa o dia citado pelo dia da semana: "nesta terça-feira (6)" → "nesta terça-feira (6 de outubro de 2026)".

    O mês é o do dia N mais próximo da data da notícia (até 10 dias antes ou depois) cujo dia da semana
    bate com o citado; sem esse encaixe, a frase fica como está.
    """
    try:
        publicacao = date.fromisoformat((data or "")[:10])
    except ValueError:
        return frase

    def completar(achado: re.Match) -> str:
        nome = (achado.group(1) or achado.group(3)).lower()
        dia = int(achado.group(2) or achado.group(4))
        candidatas = []
        for meses in (-1, 0, 1):
            ano, mes = divmod(publicacao.year * 12 + publicacao.month - 1 + meses, 12)
            try:
                candidatas.append(date(ano, mes + 1, dia))
            except ValueError:
                continue
        certas = [d for d in candidatas if DIAS_DA_SEMANA[d.weekday()] == nome and abs(d - publicacao) <= timedelta(days=10)]
        if not certas:
            return achado.group(0)
        dia_certo = min(certas, key=lambda d: abs(d - publicacao))
        return achado.group(0)[:-1] + f" de {MESES[dia_certo.month - 1]} de {dia_certo.year})"

    return DIA_RELATIVO.sub(completar, frase)


def parecidas(a: str, b: str) -> bool:
    """Repetição da mesma frase (o título repetido no primeiro parágrafo); números diferentes são fatos diferentes."""
    if sorted(q.valor for q in quantidades(a)) != sorted(q.valor for q in quantidades(b)):
        return False
    pa, pb = set(re.findall(r"\w+", sem_acentos(a))), set(re.findall(r"\w+", sem_acentos(b)))
    return bool(pa and pb) and len(pa & pb) / len(pa | pb) >= 0.8


def extrair(titulo: str, texto: str, data: str | None = None, maximo: int = MAXIMO_DE_AFIRMACOES, minimo: int = 3) -> list[Candidata]:
    """Candidatas do título e do texto, das mais fortes para as mais fracas, até `maximo`."""
    partes = [(0, titulo.strip())] if titulo.strip() else []
    for posicao, frase in enumerate(frases(texto), start=1):
        # Partes separadas por ponto e vírgula costumam ser fatos distintos.
        pedacos = [p.strip() for p in frase.split(";")]
        if len(pedacos) > 1 and all(len(p.split()) >= PALAVRAS_MINIMAS for p in pedacos):
            partes.extend((posicao, p) for p in pedacos)
        else:
            partes.append((posicao, frase))
    candidatas: list[Candidata] = []
    for posicao, frase in partes:
        pontuacao, sinais, alertas, descarte = avaliar(frase)
        if descarte or pontuacao < minimo or any(parecidas(frase, c.trecho_origem) for c in candidatas):
            continue
        fonte = FONTE_ATRIBUIDA.search(frase)
        texto_proposto = anotar_ano(datar(frase, data), data)
        if not CARACTERES_MINIMOS <= len(texto_proposto) <= CARACTERES_MAXIMOS:
            continue
        if texto_proposto != frase and "sem período: o verificador não confere a data" in alertas and not periodos(texto_proposto).vazio:
            alertas.remove("sem período: o verificador não confere a data")
            sinais.append("data completada")
        candidatas.append(Candidata(texto_proposto, frase, posicao, pontuacao, sinais, alertas, fonte.group(1).strip() if fonte else None))
    candidatas.sort(key=lambda c: (-c.pontuacao, c.posicao))
    return candidatas[:maximo]


def propor(pedido: PedidoExtracao) -> RelatorioExtracao:
    """Resposta do POST /extrair: candidatas para o publicador confirmar, editar ou descartar."""
    noticia = pedido.noticia
    avisos = ["Afirmações propostas por regras, sem verificação: confirme, edite ou descarte cada uma antes de analisar."]
    data = (noticia.data or "").strip()
    try:
        data_valida = date.fromisoformat(data).isoformat() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", data) else None
    except ValueError:
        data_valida = None
    if not data_valida:
        avisos.append("Sem data da notícia no formato AAAA-MM-DD: as datas relativas e os meses sem ano não foram completados.")
    if not noticia.texto.strip():
        avisos.append("A notícia veio sem texto: só o título foi considerado.")
    candidatas = extrair(noticia.titulo, noticia.texto, data_valida, pedido.maximo)
    return RelatorioExtracao(
        afirmacoes=[
            AfirmacaoCandidata(
                texto=c.texto, trecho_origem=c.trecho_origem, posicao=c.posicao, pontuacao=c.pontuacao,
                sinais=c.sinais, alertas=c.alertas, atribuicao=c.atribuicao,
            )
            for c in candidatas
        ],
        avisos=avisos,
        versoes={"extracao": VERSAO, "config": CONFIG["versao_config"]},
    )
