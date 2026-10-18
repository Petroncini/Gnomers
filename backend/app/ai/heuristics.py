"""Versões determinísticas das tarefas do LLM.

Servem de plano B quando o LLM não está configurado ou devolve algo inválido duas
vezes, e são o que os testes usam. São boas o bastante para nomes de catálogo de
mercado, que seguem um padrão ("Leite Uht Integral Italac 1l").
"""

from __future__ import annotations

import re

from app.preferences.rules import norm

# Sobe quando a interpretação dos itens muda; itens salvos com versão menor são reinterpretados.
PARSER_VERSION = 2

# Marcas comuns em mercado. Comparação feita sem acento e em minúsculas.
KNOWN_BRANDS = [
    "Italac", "Parmalat", "Piracanjuba", "Itambé", "Ninho", "Nestlé", "Quatá", "Jussara", "Aurora",
    "Hércules", "Agrindus", "Tirol", "Elegê", "Batavo", "Danone", "Vigor", "Molico", "Shefa",
    "Pilão", "Melitta", "3 Corações", "Café do Ponto", "Pelé", "Café Brasileiro", "Santa Clara", "Caboclo",
    "Ypê", "Limpol", "Minuano", "Veja", "Omo", "Ariel", "Brilhante", "Tixan", "Comfort",
    "Downy", "Qboa", "Pinho Sol", "Bombril", "Assolan", "Scott", "Neve", "Personal", "Mili", "Snob",
    "Camil", "Tio João", "Prato Fino", "Namorado", "Kicaldo", "Broto Legal", "Urbano",
    "Dona Benta", "Renata", "Adria", "Barilla", "Galo", "Santa Amália", "Fortaleza",
    "Liza", "Soya", "Salada", "Cocamar", "Gallo", "Andorinha", "Borges",
    "União", "Da Barra", "Guarani", "Caravelas", "Native",
    "Qualy", "Doriana", "Becel", "Delícia", "Claybom",
    "Sadia", "Perdigão", "Seara", "Aurora Alimentos", "Swift", "Friboi",
    "Pullman", "Wickbold", "Seven Boys", "Bauducco", "Plus Vita", "Visconti",
    "Heinz", "Hellmann's", "Quero", "Fugini", "Predilecta", "Elefante", "Pomarola",
    "Coca-Cola", "Guaraná Antarctica", "Pepsi", "Fanta", "Sprite", "Del Valle", "Maguary",
    "Colgate", "Sorriso", "Oral-B", "Close Up", "Dove", "Rexona", "Nivea", "Protex", "Lux", "Palmolive",
    "Carrefour", "Qualitá", "Great Value", "Taeq", "Club des Sommeliers", "Viva Mais",
    "Kellogg's", "Nescau", "Toddy", "Ovomaltine", "Lacta", "Garoto",
    "Sonhos de Leite", "Cocomix", "Belvita", "Vitarella", "Piraquê", "Marilan", "Mabel",
    "Yoki", "Kitano", "Knorr", "Maggi", "Sazón", "Arisco",
]
_BRANDS_NORM = sorted(((norm(b), b) for b in KNOWN_BRANDS), key=lambda x: -len(x[0]))


# Termos descritivos que entram na query e que, quando pedidos, precisam aparecer no SKU.
VARIETY_TERMS = [
    "integral", "desnatado", "semidesnatado", "zero lactose", "sem lactose", "caipira", "extraforte",
    "tradicional", "orgânico", "light", "zero", "diet", "parboilizado", "agulhinha", "carioca", "preto",
    "extra virgem", "refinado", "cristal", "mascavo", "demerara", "com sal", "sem sal", "fatiado",
    "concentrado", "neutro", "limão", "coco", "lavanda", "tipo 1", "tipo a", "uht", "pasteurizado",
    "sem glúten", "vegano", "maduro", "verde", "vermelho", "branco",
]
_VARIETY_NORM = sorted({norm(v) for v in VARIETY_TERMS}, key=len, reverse=True)

# Palavras que, logo depois do nome da categoria, transformam o produto em outra coisa
# ("leite de coco", "leite condensado", "café com leite").
CATEGORY_CHANGERS = {
    "coco", "condensado", "infantil", "formula", "em", "funcionai", "funcionais", "po", "fermentado", "vegetal", "amendoas", "aveia", "soja", "achocolatado",
    "creme", "doce", "moça", "sabor", "chocolate", "solúvel", "soluvel", "capsula", "capsulas",
    "instantaneo", "bolo", "biscoito", "barra", "sorvete", "iogurte", "molho", "farinha", "tempero",
    "cappuccino", "capuccino", "mocaccino", "latte", "drip", "sache", "sachet", "individual",
}

# Cabeças genéricas que só fazem sentido com a palavra seguinte ("papel higiênico").
GENERIC_HEADS = {"papel", "creme", "sabao", "molho", "farinha", "extrato", "agua", "oleo", "pasta",
                 "queijo", "suco", "file", "peito", "carne", "leite"}

STOPWORDS = {"de", "da", "do", "das", "dos", "com", "e", "em", "para", "pra", "o", "a", "os", "as", "um", "uma"}

_PACK_RE = re.compile(
    r"(?P<n>\d+(?:[.,]\d+)?)\s*(?P<u>kg|quilos?|kilos?|g|gr|gramas?|mg|l|litros?|lt|ml|un|unid|unidades?|rolos?|pcs?|pe[cç]as?|folhas?)\b",
    re.IGNORECASE,
)
_MULTI_RE = re.compile(r"(?:com|c/|leve|kit|pack|pacote com|caixa com)\s*(?P<n>\d+)\s*(?:un|unid|unidades?|rolos?)?\b", re.I)
_TIMES_RE = re.compile(r"\b(?P<n>\d+)\s*x\s*(?P<s>\d+(?:[.,]\d+)?)\s*(?P<u>kg|g|l|ml)\b", re.I)


def parse_pack(text: str) -> tuple[float | None, str | None]:
    """Extrai o tamanho total da embalagem, em g, ml ou unidades."""
    t = text.replace(" ", " ")
    m = _TIMES_RE.search(t)
    if m:
        size, unit = _to_base(float(m["s"].replace(",", ".")), m["u"])
        return (size * int(m["n"]) if size else None), unit
    best: tuple[float | None, str | None] = (None, None)
    for m in _PACK_RE.finditer(t):
        size, unit = _to_base(float(m["n"].replace(",", ".")), m["u"])
        if unit in ("g", "ml"):
            best = (size, unit)
            break
        if best[0] is None:
            best = (size, unit)
    if best[0] is None:
        m = _MULTI_RE.search(t)
        if m:
            best = (float(m["n"]), "un")
    return best


def _to_base(value: float, unit: str) -> tuple[float, str]:
    u = norm(unit)
    if u.startswith("kg") or u.startswith("quilo") or u.startswith("kilo"):
        return value * 1000, "g"
    if u in ("g", "gr") or u.startswith("grama"):
        return value, "g"
    if u == "mg":
        return value / 1000, "g"
    if u in ("l", "lt") or u.startswith("litro"):
        return value * 1000, "ml"
    if u == "ml":
        return value, "ml"
    return value, "un"


def find_brand(text: str) -> str | None:
    t = f" {norm(text)} "
    for key, original in _BRANDS_NORM:
        if f" {key} " in t:
            return original
    return None


def find_variety(text: str) -> list[str]:
    t = f" {norm(text)} "
    found: list[str] = []
    for v in _VARIETY_NORM:
        if f" {v} " in t and not any(v in f for f in found):
            found.append(v)
    return found


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", norm(text))]


def singular(word: str) -> str:
    if len(word) > 4 and word.endswith("oes"):
        return word[:-3] + "ao"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


_QTY_RE = re.compile(r"^\s*(?P<n>\d+(?:[.,]\d+)?)\s*(?:x\s+|un\s+|unidades?\s+|pacotes?\s+(?:de\s+)?|caixas?\s+(?:de\s+)?)?(?=[a-zà-ú])", re.I)
_QTY_SUFFIX_RE = re.compile(r"\s+x\s*(?P<n>\d+)\s*$", re.I)


def parse_item(raw: str) -> dict:
    """Texto livre do item ("2 leite integral Italac 1L") para os campos do ListItem."""
    text = raw.strip()
    quantity = 1.0
    m = _QTY_SUFFIX_RE.search(text)
    if m:
        quantity = float(m["n"])
        text = text[: m.start()].strip()
    else:
        m = _QTY_RE.match(text)
        # "1kg de arroz" é tamanho, não quantidade
        if m and not re.match(r"^\s*\d+(?:[.,]\d+)?\s*(kg|g|l|ml|litros?|quilos?|kilos?|gramas?)\b", text, re.I):
            quantity = float(m["n"].replace(",", "."))
            text = text[m.end():].strip()

    pack_size, pack_unit = parse_pack(text)
    brand = find_brand(text)
    variety = find_variety(text)
    query_text = re.sub(r"\s+", " ", _PACK_RE.sub("", text)).strip(" -,")
    query_text = re.sub(r"^(?:de|do|da)\s+", "", query_text, flags=re.I)
    category = _category(query_text, brand, variety)
    query = query_text
    return {
        "query": query or text,
        "intention": "product_exact" if brand else "product",
        "category": category,
        "brand": brand,
        "variety_terms": variety,
        "pack_size": pack_size,
        "pack_unit": pack_unit,
        "quantity": quantity,
    }


def _category(text: str, brand: str | None, variety: list[str]) -> str:
    skip = set(_tokens(brand or "")) | {t for v in variety for t in v.split()}
    words = [singular(w) for w in _tokens(text) if w not in STOPWORDS and not w.isdigit() and w not in skip]
    if not words:
        return norm(text)
    if words[0] in GENERIC_HEADS and len(words) > 1 and words[0] != "leite":
        return f"{words[0]} {words[1]}"
    return words[0]


def is_derivative(category: str, name: str, item_query: str = "") -> bool:
    """True quando o nome traz a categoria seguida de algo que vira outro produto ("Leite em Pó", "Leite de Coco")."""
    tokens = [singular(t) for t in _tokens(name)]
    cat_tokens = singular(norm(category)).split()
    if not cat_tokens or cat_tokens[-1] not in tokens:
        return False
    query_tokens = {singular(t) for t in _tokens(item_query)}
    idx = tokens.index(cat_tokens[-1])
    # olha as duas palavras seguintes: "Café com Leite Solúvel" esconde o "solúvel" atrás de "leite"
    following = [t for t in tokens[idx + 1: idx + 4] if t not in STOPWORDS][:2]
    return any(t in CATEGORY_CHANGERS and t not in query_tokens for t in following)


def normalize_product(category: str, name: str, item_query: str = "") -> dict:
    """Nome de um SKU do catálogo para marca, variedade, tamanho e se casa com o item."""
    size, unit = parse_pack(name)
    brand = find_brand(name)
    variety = find_variety(name)
    tokens = [singular(t) for t in _tokens(name)]
    cat = singular(norm(category))
    query_tokens = {singular(t) for t in _tokens(item_query)}

    cat_tokens = cat.split()
    matches = False
    if cat_tokens and all(c in tokens for c in cat_tokens):
        idx = tokens.index(cat_tokens[-1])
        # a categoria precisa abrir o nome (ou vir logo depois de um descritor curto)
        matches = tokens.index(cat_tokens[0]) <= 2
        following = [t for t in tokens[idx + 1: idx + 3] if t not in STOPWORDS]
        if following and following[0] in CATEGORY_CHANGERS and following[0] not in query_tokens:
            matches = False

    return {
        "brand": brand,
        "variety_terms": variety,
        "pack_size": size,
        "pack_unit": unit,
        "matches": matches,
    }


# Sinônimos de rótulo: a busca do iFood exige todas as palavras e não conhece sinônimo
# ("granola sem açúcar" não acha "Granola Zero Açúcar"; sandbox, 18/10).
LABEL_SYNONYMS = [
    ["sem açúcar", "zero açúcar", "zero açúcares", "0% açúcar"],
    ["sem lactose", "zero lactose"],
    ["sem glúten", "gluten free"],
    ["desnatado", "0% gordura"],
    ["moído", "torrado e moído"],
]

# Palavras que descrevem o produto mas não aparecem em rótulo; na busca, só eliminam resultados.
_NON_LABEL = re.compile(
    r"\((?:[^)]*)\)|[<>]=?\s*\d+\S*|\b(?:cru[ao]?s?|em gr[aã]os?|(?:pra|para|de) filtro|kilos?|quilos?|"
    r"pacotes? de|unidades? de|do bom|bem)\b",
    re.I,
)


def term_variants(term: str) -> list[str]:
    """O termo e seus sinônimos de rótulo, normalizados."""
    t = norm(term)
    for group in LABEL_SYNONYMS:
        if t in (norm(g) for g in group):
            return [norm(g) for g in group]
    return [t]


_ACCENTS = {"a": "aáàâã", "e": "eéê", "i": "ií", "o": "oóôõ", "u": "uúü", "c": "cç"}


def _loose(text: str) -> str:
    """Regex que casa o texto com ou sem acento."""
    return "".join(f"[{_ACCENTS[ch]}]" if ch in _ACCENTS else re.escape(ch) for ch in norm(text))


def has_term(text: str, term: str) -> bool:
    """O texto (nome do SKU) traz o termo ou um sinônimo dele."""
    t = f" {norm(text)} "
    return any(f" {v} " in t or (len(v) > 4 and v in t) for v in term_variants(term))


def clean_query(text: str) -> str:
    """Tira da busca o que não vai em rótulo ("feijão cru em grão (>500g)" -> "feijão")."""
    out = _NON_LABEL.sub(" ", text)
    out = _PACK_RE.sub(" ", out)
    out = re.sub(r"\b\d+(?:[.,]\d+)?\b", " ", out)
    out = re.sub(r"\s+", " ", out).strip(" -,.")
    return out or text.strip()


def core_query(category: str, query: str, brand: str | None = None) -> str:
    """Busca curta: só as palavras do produto (e a marca), a rede de segurança da busca."""
    cat = {singular(w) for w in norm(category).split()}
    words = [w for w in query.split() if singular(norm(w)) in cat]
    core = " ".join(dict.fromkeys(words)) or category
    if brand and norm(brand) not in norm(core):
        core = f"{core} {brand}"
    return core


def query_variants(query: str) -> list[str]:
    """A mesma busca com o sinônimo de rótulo trocado ("granola sem açúcar" -> "granola zero açúcar")."""
    out = []
    for group in LABEL_SYNONYMS:
        for v in group:
            pattern = re.compile(rf"(?<!\w){_loose(v)}(?!\w)", re.I)
            if pattern.search(query):
                out += [pattern.sub(alt, query) for alt in group if alt != v and "%" not in alt]
                break
    return out


def score_notes(notes: list[str], names: list[str]) -> list[float]:
    """Sem LLM, uma nota só pontua quando os termos dela aparecem no nome do SKU."""
    terms = {t for n in notes for t in _tokens(n) if t not in STOPWORDS and len(t) > 3}
    out = []
    for name in names:
        toks = set(_tokens(name))
        out.append(1.0 if terms & toks else 0.5)
    return out
