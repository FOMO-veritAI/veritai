"""Verificador de entidade, período e número entre a afirmação e cada janela levada ao NLI.

Só rebaixa: um apoio ou uma contradição dados pelo NLI podem virar neutros quando a
janela trata de outra entidade, de outro período ou não traz o número da afirmação.
Nenhuma relação é criada aqui, então o verificador não gera aprovações novas.

Regras (todas de operação, não validadas em dados rotulados revisados):
- entidade: nomes próprios e siglas da afirmação precisam aparecer no documento ou no
  título da fonte; senão, a janela não apoia nem contradiz;
- período: se a afirmação e a janela citam períodos explícitos (mês e ano, trimestre e
  ano, ciclo ou ano solto) e nenhum coincide, a janela não apoia nem contradiz;
- número: só há apoio se cada quantidade da afirmação aparece na janela, com o mesmo
  sinal e a mesma unidade (% ou p.p.).
"""

import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
ORDINAIS = {"primeiro": 1, "segundo": 2, "terceiro": 3, "quarto": 4, "1": 1, "2": 2, "3": 3, "4": 4}

PALAVRA = r"[\wÀ-ÿ]+(?:-[\wÀ-ÿ]+)*"
# Palavras com maiúscula que não são entidades (início de frase, meses, artigos e conectivos).
NAO_ENTIDADES = {
    "o", "a", "os", "as", "em", "no", "na", "nos", "nas", "de", "do", "da", "dos", "das", "segundo", "segunda", "entre", "há",
    "um", "uma", "para", "com", "por", "pelo", "pela", "se", "que", "e", "ou", "nesta", "neste", "este", "esta", "esse", "essa",
    "frente", "ante", "até", "após", "desde", "mais", "menos", "cerca", "apenas", *MESES,
    # O país fica implícito nas estatísticas nacionais ("taxa de desocupação" = do Brasil).
    "brasil",
}
# Entidades que a fonte pode citar por outro termo.
EQUIVALENTES: dict[str, tuple[str, ...]] = {}
QUEDA = re.compile(r"\b(ca[ií]\w*|caem|recu\w*|queda\w*|diminu\w*|redu[çz]\w*|retra\w*|baix\w*|defla\w*|negativ\w*|perd\w*|encolh\w*)\b", re.IGNORECASE)
COMPARACAO = re.compile(r"\b(?:frente|ante|em relação|comparad\w*|abaixo|acima|contra|do que|após)\b", re.IGNORECASE)
FUTURO = re.compile(r"\b(ser[áã]o?|v[ãa]o?|dever[áã]o?|deve|devem|previst\w*|pr[óo]xim\w*|a partir de)\b", re.IGNORECASE)

# "milh" antes de "mil": "milhões" também começa com "mil".
MULTIPLICADORES = {"milh": 1e6, "bilh": 1e9, "trilh": 1e12, "mil": 1e3}
NUMERO = re.compile(
    r"(?P<sinal>[-–−]\s?)?(?P<inteiro>\d{1,3}(?:\.\d{3})+|\d+)(?:,(?P<decimal>\d+))?"
    r"(?P<ordinal>[ªº°])?"
    r"(?:\s*(?P<unidade>%|p\.\s?p\.|pontos? percentua\w*|mil\b|milh\w*|bilh\w*|trilh\w*))?",
    re.IGNORECASE,
)


def sem_acentos(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def tokens(texto: str) -> set[str]:
    return {sem_acentos(t) for t in re.findall(PALAVRA, texto)}


# ---------------------------------------------------------------- entidades


def entidades(afirmacao: str) -> list[str]:
    """Nomes próprios e siglas: palavras com maiúscula fora do início da frase, siglas e códigos (IPCA-15)."""
    resultado = []
    for achado in re.finditer(PALAVRA, afirmacao):
        palavra = achado.group()
        if not palavra[0].isupper() or sem_acentos(palavra) in NAO_ENTIDADES:
            continue
        sigla = len(palavra) >= 2 and (palavra.isupper() or any(c.isdigit() for c in palavra))
        inicio_de_frase = not afirmacao[: achado.start()].strip() or afirmacao[: achado.start()].rstrip().endswith((".", ":", "!", "?"))
        if sigla or not inicio_de_frase:
            resultado.append(palavra)
    return list(dict.fromkeys(resultado))


def entidade_ausente(afirmacao: str, documento: str, titulo: str = "", dominio: str = "") -> str | None:
    """A primeira entidade da afirmação que não aparece no documento, no título nem no domínio da fonte."""
    presentes = tokens(documento) | tokens(titulo)
    dominio = sem_acentos(dominio)
    for entidade in entidades(afirmacao):
        chave = sem_acentos(entidade)
        aceitas = EQUIVALENTES.get(chave, (chave,))
        # Comparação por palavra inteira: "IPCA" não está em "IPCA-15".
        if not any(sem_acentos(a) in presentes for a in aceitas) and chave not in dominio.split("."):
            return entidade
    return None


# ---------------------------------------------------------------- períodos


@dataclass
class Periodos:
    meses: set[tuple[int, int]]
    trimestres: set[tuple[int, int]]
    ciclos: set[str]
    anos_soltos: set[int]

    @property
    def vazio(self) -> bool:
        return not (self.meses or self.trimestres or self.ciclos or self.anos_soltos)


def periodos(texto: str) -> Periodos:
    texto = texto.lower()
    meses, trimestres, ciclos = set(), set(), set()
    ocupado = [False] * len(texto)

    def marcar(achado: re.Match) -> None:
        for i in range(achado.start(), achado.end()):
            ocupado[i] = True

    for achado in re.finditer(r"\b(20\d{2})/(\d{2})\b", texto):
        ciclos.add(achado.group())
        marcar(achado)
    padrao_mes = r"\b(" + "|".join(MESES) + r")\s*(?:de\s+|/\s*)?(\d{4})\b"
    for achado in re.finditer(padrao_mes, texto):
        meses.add((MESES.index(achado.group(1)) + 1, int(achado.group(2))))
        marcar(achado)
    padrao_tri = r"\b(primeiro|segundo|terceiro|quarto|[1-4])\s*[ºo°]?\s+trimestre\s+(?:de\s+)?(\d{4})\b"
    for achado in re.finditer(padrao_tri, texto):
        trimestres.add((ORDINAIS[achado.group(1)], int(achado.group(2))))
        marcar(achado)
    anos = {int(a.group()) for a in re.finditer(r"\b(19|20)\d{2}\b", texto) if not ocupado[a.start()]}
    return Periodos(meses, trimestres, ciclos, anos)


def periodo_divergente(afirmacao: Periodos, janela: Periodos) -> bool:
    """True quando os dois citam períodos explícitos do mesmo tipo e nenhum coincide."""
    if afirmacao.meses and janela.meses:
        return not afirmacao.meses & janela.meses
    if afirmacao.trimestres and janela.trimestres:
        return not afirmacao.trimestres & janela.trimestres
    if afirmacao.ciclos and janela.ciclos:
        return not afirmacao.ciclos & janela.ciclos
    # Afirmação anual ("em 2025") contra uma janela que só fala de meses ou trimestres daquele ano
    # também diverge: o dado de um trimestre não é o do ano.
    if afirmacao.anos_soltos and not (afirmacao.meses or afirmacao.trimestres):
        anos_da_janela = janela.anos_soltos | {a for _, a in janela.meses} | {a for _, a in janela.trimestres}
        if not anos_da_janela:
            return False
        return not afirmacao.anos_soltos & janela.anos_soltos
    return False


def ano_de_referencia(mes: int, publicacao: date) -> int:
    """O mês citado sem ano é o mais recente com esse nome até a data de publicação."""
    return publicacao.year if mes <= publicacao.month else publicacao.year - 1


def anotar_ano(janela: str, data_publicacao: str | None) -> str:
    """Acrescenta o ano aos meses citados sem ano, para o NLI (o texto exibido não muda).

    Não anota datas com dia ("8 de novembro"), que costumam ser eventos, nem janelas com
    marcas de futuro ("serão", "previsto"), em que o mês pode ser posterior à publicação.
    """
    try:
        publicacao = date.fromisoformat((data_publicacao or "")[:10])
    except ValueError:
        return janela
    if FUTURO.search(janela):
        return janela
    partes, fim = [], 0
    for achado in re.finditer(r"\b(" + "|".join(MESES) + r")\b", janela, re.IGNORECASE):
        depois = janela[achado.end() : achado.end() + 9]
        antes = janela[max(0, achado.start() - 6) : achado.start()]
        if re.match(r"\s*(?:de\s+|/\s*)?\d{4}", depois) or re.search(r"\d{1,2}\s+de\s+$", antes):
            continue
        ano = ano_de_referencia(MESES.index(achado.group(1).lower()) + 1, publicacao)
        partes.append(janela[fim : achado.end()] + f" de {ano}")
        fim = achado.end()
    return "".join(partes) + janela[fim:]


# ---------------------------------------------------------------- números


@dataclass(frozen=True)
class Quantidade:
    valor: float  # absoluto, já com o multiplicador (mil, milhões...)
    negativo: bool
    unidade: str  # "pct", "pp" ou ""
    casas: int | None  # casas decimais escritas; None para inteiros
    multiplicador: float = 1.0
    inicio: int = field(default=0, compare=False)
    fim: int = field(default=0, compare=False)


def quantidades(texto: str) -> list[Quantidade]:
    resultado = []
    for achado in NUMERO.finditer(texto):
        antes = texto[max(0, achado.start() - 1) : achado.start()]
        depois = texto[achado.end() : achado.end() + 12]
        sinal, inteiro, decimal, unidade = achado.group("sinal"), achado.group("inteiro"), achado.group("decimal"), (achado.group("unidade") or "").lower()
        # Números colados a palavras (IPCA-15, Covid-19, 20-valente), ordinais (13ª) e datas (8 de novembro) não são quantidades.
        if (antes and (antes.isalnum() or antes in ",.")) or achado.group("ordinal") or re.match(r"-\w", depois):
            continue
        if sinal and achado.start() > 0 and texto[achado.start() - 1].isalnum():
            continue
        if not decimal and not unidade and (re.match(r"\s+de\s+(" + "|".join(MESES) + r")\b", depois, re.IGNORECASE) or re.match(r"(19|20)\d{2}$", inteiro)):
            continue
        valor = float(inteiro.replace(".", "") + ("." + decimal if decimal else ""))
        tipo = "pct" if unidade == "%" else "pp" if unidade.startswith(("p.", "ponto")) else ""
        multiplicador = next((m for prefixo, m in MULTIPLICADORES.items() if unidade.startswith(prefixo)), 1.0)
        resultado.append(Quantidade(valor * multiplicador, bool(sinal), tipo, len(decimal) if decimal else None, multiplicador, achado.start(), achado.end()))
    return resultado


@dataclass
class Contexto:
    """O que cerca uma quantidade na frase: períodos do seu trecho e direção (queda ou não)."""

    periodos: Periodos
    queda: bool


def frases(texto: str) -> list[tuple[int, int]]:
    # Divide só antes de maiúscula, para não partir em "p.p. abaixo".
    limites = [0] + [m.end() for m in re.finditer(r"[.!?]\s+(?=[A-ZÀ-Ý])", texto)] + [len(texto)]
    return list(zip(limites, limites[1:]))


def contextos(texto: str, qs: list[Quantidade]) -> list[Contexto]:
    resultado = []
    for inicio_frase, fim_frase in frases(texto):
        da_frase = [q for q in qs if inicio_frase <= q.inicio < fim_frase]
        for i, q in enumerate(da_frase):
            antes = da_frase[i - 1].fim if i else inicio_frase
            depois = da_frase[i + 1].inicio if i + 1 < len(da_frase) else fim_frase
            # Períodos de comparação ("frente a julho", "acima de maio") não são o período do número.
            anterior_ao_numero = COMPARACAO.split(texto[antes : q.inicio])[-1]
            posterior_ao_numero = COMPARACAO.split(texto[q.fim : depois])[0]
            trecho = anterior_ao_numero + " " + posterior_ao_numero
            proprios = periodos(trecho)
            # Sem período no próprio trecho, vale o da frase ("é de 5,3% e ... no trimestre encerrado em agosto").
            if not (proprios.meses or proprios.trimestres):
                proprios = periodos(texto[inicio_frase:fim_frase])
            # Direção: sinal ou verbo de queda logo antes do número; "caiu para 5,3%" é nível, não variação.
            anterior = texto[antes : q.inicio]
            queda = QUEDA.search(anterior)
            resultado.append(Contexto(proprios, q.negativo or bool(queda and not re.search(r"\bpara\b", anterior[queda.end() :]))))
    return resultado


def mesmo_periodo(alegado: Periodos, encontrado: Periodos) -> bool:
    if alegado.meses and encontrado.meses:
        return bool(alegado.meses & encontrado.meses)
    if alegado.trimestres and encontrado.trimestres:
        return bool(alegado.trimestres & encontrado.trimestres)
    return True


def mesmo_valor(alegado: Quantidade, encontrado: Quantidade) -> bool:
    if alegado.casas is not None:
        # Decimal escrito: igual até a última casa da afirmação ("0,7" = "0,70"; "34,08 milhões" = 34.080.000).
        tolerancia = 0.5 * 10 ** (-alegado.casas) * alegado.multiplicador
        return math.isclose(alegado.valor, encontrado.valor, abs_tol=tolerancia + 1e-9)
    # Inteiro: os algarismos significativos da afirmação são o começo do número encontrado,
    # com a mesma ordem de grandeza ("152 mil" ~ 152.966; "1.300" não é 13.241).
    alegado_txt, encontrado_txt = str(int(round(alegado.valor))), str(int(encontrado.valor))
    return len(alegado_txt) == len(encontrado_txt) and encontrado_txt.startswith(alegado_txt.rstrip("0") or "0")


def numero_ausente(afirmacao: str, janela: str) -> Quantidade | None:
    """A primeira quantidade da afirmação sem correspondente na janela.

    Correspondente: mesmo valor e unidade (% ou p.p.), mesma direção ("caiu 0,25%" = "-0,25%";
    "subiram 0,8%" ≠ "caiu 0,8%") e, se a afirmação cita mês ou trimestre, ligado a esse período
    na janela ("-0,32% em agosto" não sustenta "-0,32% em julho").
    """
    alegados, da_janela = quantidades(afirmacao), quantidades(janela)
    ctx_alegados, ctx_janela = contextos(afirmacao, alegados), contextos(janela, da_janela)
    periodo_alegado = periodos(afirmacao)
    for alegado, ctx_a in zip(alegados, ctx_alegados):
        if not any(
            (not alegado.unidade or alegado.unidade == q.unidade)
            and mesmo_valor(alegado, q)
            and ctx_a.queda == ctx_q.queda
            and mesmo_periodo(periodo_alegado, ctx_q.periodos)
            for q, ctx_q in zip(da_janela, ctx_janela)
        ):
            return alegado
    return None


# ---------------------------------------------------------------- decisão


def bloqueios(afirmacao: str, janela: str, documento: str, titulo: str = "", dominio: str = "") -> dict[str, str]:
    """Rótulos do NLI que esta janela não pode sustentar, com o motivo: {"entailment": ..., "contradiction": ...}."""
    if entidade := entidade_ausente(afirmacao, documento, titulo, dominio):
        motivo = f"entidade ausente: {entidade}"
        return {"entailment": motivo, "contradiction": motivo}
    if periodo_divergente(periodos(afirmacao), periodos(janela)):
        return {"entailment": "período divergente", "contradiction": "período divergente"}
    if numero := numero_ausente(afirmacao, janela):
        sinal = "-" if numero.negativo else ""
        return {"entailment": f"número sem correspondente: {sinal}{numero.valor:g}"}
    return {}


def ajustar(probabilidades: dict[str, float], bloqueados: dict[str, str]) -> dict[str, float]:
    """Move a probabilidade dos rótulos bloqueados para neutral."""
    ajustadas = dict(probabilidades)
    for rotulo in bloqueados:
        ajustadas["neutral"] = ajustadas.get("neutral", 0.0) + ajustadas.get(rotulo, 0.0)
        ajustadas[rotulo] = 0.0
    return ajustadas
