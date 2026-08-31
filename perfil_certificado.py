"""Engine de renderização de certificados a partir de perfis.

Um *perfil* é um JSON em ``perfis/`` que descreve um modelo de certificado: o PDF
base, os campos de texto a sobrepor (posição, fonte, alinhamento) e o bloco de QR
Code. Nenhuma coordenada de layout fica no app — tudo vem do perfil, para que
eventos futuros com desenhos diferentes sejam só mais um arquivo.

Nenhuma função aqui deixa vazar caminho absoluto ou detalhe interno em mensagem
de erro destinada à tela: ``PerfilInvalido`` carrega texto seguro.
"""

import io
import json
import re
import unicodedata
from pathlib import Path

import fitz  # PyMuPDF
import qrcode

APP_DIR = Path(__file__).parent
PERFIS_DIR = APP_DIR / "perfis"

ALINHAMENTOS = {"left", "center", "right"}
TIPOS_CAMPO = {"linha"}

FONTE_PADRAO = "helv"
COR_PADRAO = (0.02, 0.13, 0.34)
BRANCO = (1, 1, 1)

# Chaves de dados que o app sabe preencher. Um campo do perfil que referencie
# outra chave é aceito na validação, mas fica em branco se o dado não vier.
CHAVES_DADOS = {"nome", "cpf", "tema", "horas", "grupo"}


class PerfilInvalido(ValueError):
    """Perfil malformado ou incompleto. A mensagem é segura para exibir."""


def normalizar_grupo(valor) -> str:
    """Minúsculas, sem acento e sem separadores. 'Só manhã ' -> 'somanha'.

    Serve para casar o valor da coluna 'Grupo' da planilha com o ``filtro_grupo``
    do perfil sem depender de acento, caixa ou espaço.
    """
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[\s_\-.]", "", texto).strip().lower()


def filtro_grupo(perfil: dict) -> list:
    """Lista normalizada de grupos aceitos pelo perfil, ou ``[]`` se não filtra."""
    return [normalizar_grupo(v) for v in (perfil.get("filtro_grupo") or [])]


def grupo_aceito(perfil: dict, valor) -> bool:
    """Se o perfil não declara ``filtro_grupo``, aceita qualquer grupo."""
    aceitos = filtro_grupo(perfil)
    return not aceitos or normalizar_grupo(valor) in aceitos


def _num(valor, campo_nome, contexto):
    try:
        return float(valor)
    except (TypeError, ValueError):
        raise PerfilInvalido(f"{contexto}: '{campo_nome}' precisa ser um número.")


def _resolver_pdf(perfil: dict) -> Path:
    caminho = (perfil.get("pdf") or "").strip()
    if not caminho:
        raise PerfilInvalido(f"Perfil '{perfil.get('id', '?')}' não informa 'pdf'.")
    pdf = Path(caminho)
    if not pdf.is_absolute():
        pdf = APP_DIR / pdf
    return pdf


def validar_perfil(perfil: dict) -> dict:
    """Valida o dicionário de um perfil. Levanta ``PerfilInvalido`` se algo falta.

    Devolve o próprio perfil para encadear.
    """
    if not isinstance(perfil, dict):
        raise PerfilInvalido("Perfil não é um objeto JSON.")

    ident = str(perfil.get("id") or "").strip()
    if not ident:
        raise PerfilInvalido("Perfil sem 'id'.")
    if not str(perfil.get("nome") or "").strip():
        raise PerfilInvalido(f"Perfil '{ident}' sem 'nome'.")

    pdf = _resolver_pdf(perfil)
    if not pdf.is_file():
        raise PerfilInvalido(
            f"Perfil '{ident}': PDF não encontrado em '{perfil.get('pdf')}'."
        )
    try:
        fitz.open(pdf).close()
    except Exception as exc:  # PDF corrompido ou ilegível — perfil inutilizável.
        raise PerfilInvalido(
            f"Perfil '{ident}': o PDF '{perfil.get('pdf')}' não pôde ser aberto ({type(exc).__name__})."
        )

    campos = perfil.get("campos")
    if not isinstance(campos, list) or not campos:
        raise PerfilInvalido(f"Perfil '{ident}' não tem 'campos'.")

    vistos = set()
    for i, campo in enumerate(campos):
        ctx = f"Perfil '{ident}', campo #{i + 1}"
        if not isinstance(campo, dict):
            raise PerfilInvalido(f"{ctx}: não é um objeto.")
        chave = str(campo.get("campo") or "").strip()
        if not chave:
            raise PerfilInvalido(f"{ctx}: sem 'campo'.")
        if chave in vistos:
            raise PerfilInvalido(f"Perfil '{ident}': campo '{chave}' duplicado.")
        vistos.add(chave)
        tipo = campo.get("tipo", "linha")
        if tipo not in TIPOS_CAMPO:
            raise PerfilInvalido(f"{ctx} ('{chave}'): tipo '{tipo}' não suportado.")
        if "x" not in campo or "y" not in campo:
            raise PerfilInvalido(f"{ctx} ('{chave}'): faltam 'x' e/ou 'y'.")
        _num(campo["x"], "x", f"{ctx} ('{chave}')")
        _num(campo["y"], "y", f"{ctx} ('{chave}')")
        alinhamento = campo.get("alinhamento", "left")
        if alinhamento not in ALINHAMENTOS:
            raise PerfilInvalido(
                f"{ctx} ('{chave}'): alinhamento '{alinhamento}' inválido "
                f"(use {', '.join(sorted(ALINHAMENTOS))})."
            )

    qr = perfil.get("qr")
    if qr is not None:
        if not isinstance(qr, dict):
            raise PerfilInvalido(f"Perfil '{ident}': 'qr' não é um objeto.")
        if qr.get("mostrar", True):
            if "x" not in qr or "y" not in qr:
                raise PerfilInvalido(f"Perfil '{ident}': 'qr' sem 'x'/'y'.")
            _num(qr["x"], "x", f"Perfil '{ident}', qr")
            _num(qr["y"], "y", f"Perfil '{ident}', qr")

    obrig = perfil.get("colunas_obrigatorias", [])
    if not isinstance(obrig, list):
        raise PerfilInvalido(f"Perfil '{ident}': 'colunas_obrigatorias' deve ser lista.")

    grupos = perfil.get("filtro_grupo")
    if grupos is not None:
        if not isinstance(grupos, list) or not grupos:
            raise PerfilInvalido(
                f"Perfil '{ident}': 'filtro_grupo' deve ser uma lista não vazia."
            )
        if any(not str(g).strip() for g in grupos):
            raise PerfilInvalido(
                f"Perfil '{ident}': 'filtro_grupo' tem um valor vazio."
            )

    return perfil


def carregar_perfil(identificador: str) -> dict:
    """Lê ``perfis/<identificador>.json`` (ou um caminho direto) e valida."""
    alvo = Path(identificador)
    if alvo.suffix != ".json":
        alvo = PERFIS_DIR / f"{identificador}.json"
    if not alvo.is_file():
        raise PerfilInvalido(f"Perfil '{identificador}' não existe.")
    try:
        perfil = json.loads(alvo.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PerfilInvalido(f"Perfil '{identificador}': JSON inválido ({exc.msg}).")
    return validar_perfil(perfil)


def listar_perfis() -> list:
    """Devolve ``[{'id', 'nome', 'arquivo'}]`` dos perfis válidos, ordenados por nome.

    Perfis inválidos são ignorados silenciosamente aqui — a validação com
    mensagem acontece quando o operador seleciona um.
    """
    encontrados = []
    if not PERFIS_DIR.is_dir():
        return encontrados
    for arquivo in sorted(PERFIS_DIR.glob("*.json")):
        try:
            perfil = validar_perfil(json.loads(arquivo.read_text(encoding="utf-8")))
        except (PerfilInvalido, json.JSONDecodeError, OSError):
            continue
        encontrados.append(
            {"id": perfil["id"], "nome": perfil["nome"], "arquivo": arquivo}
        )
    encontrados.sort(key=lambda p: p["nome"].lower())
    return encontrados


def qr_png_bytes(data: str) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    saida = io.BytesIO()
    img.save(saida, format="PNG")
    return saida.getvalue()


_FONTES = {}


def _fonte(nome: str):
    if nome not in _FONTES:
        try:
            _FONTES[nome] = fitz.Font(nome)
        except Exception:
            _FONTES[nome] = fitz.Font(FONTE_PADRAO)
    return _FONTES[nome]


def _largura_texto(texto: str, fonte: str, tamanho: float) -> float:
    """Largura real do texto renderizado.

    fitz.Font.text_length bate exatamente com o que insert_text desenha —
    fitz.get_text_length subestima ~5% em texto com acento e faz o autosize
    deixar o tema passar por cima do 'com duração de'.
    """
    return _fonte(fonte).text_length(texto, tamanho)


def _texto_centralizado_em(page, rect, texto, y, tamanho):
    largura = _largura_texto(texto, FONTE_PADRAO, tamanho)
    x = rect.x0 + (rect.width - largura) / 2
    page.insert_text(
        (x, y), texto, fontsize=tamanho, fontname=FONTE_PADRAO, color=(0.05, 0.05, 0.05)
    )


def _desenhar_linha(page, campo: dict, texto: str) -> None:
    if campo.get("maiusculas"):
        texto = texto.upper()
    fonte = campo.get("fonte", FONTE_PADRAO)
    tamanho = float(campo.get("tamanho", 12))
    tamanho_min = float(campo.get("tamanho_min", min(tamanho, 8)))
    largura_max = campo.get("largura_max")

    if largura_max:
        largura_max = float(largura_max)
        while tamanho > tamanho_min and _largura_texto(texto, fonte, tamanho) > largura_max:
            tamanho -= 0.5

    largura = _largura_texto(texto, fonte, tamanho)
    x = float(campo["x"])
    alinhamento = campo.get("alinhamento", "left")
    if alinhamento == "center":
        x -= largura / 2
    elif alinhamento == "right":
        x -= largura

    cor = tuple(campo.get("cor", COR_PADRAO))
    page.insert_text(
        (x, float(campo["y"])), texto, fontsize=tamanho, fontname=fonte, color=cor
    )


def _desenhar_qr(page, qr: dict, codigo: str, url_validacao: str) -> None:
    lado = float(qr.get("tamanho", 70))
    x = float(qr["x"])
    y = float(qr["y"])
    rect = fitz.Rect(x, y, x + lado, y + lado)
    fundo = fitz.Rect(rect.x0 - 20, rect.y0 - 8, rect.x1 + 20, rect.y1 + 32)
    page.draw_rect(fundo, color=BRANCO, fill=BRANCO, overlay=True)
    page.insert_image(rect, stream=qr_png_bytes(url_validacao), overlay=True)
    if qr.get("rotulo", True):
        _texto_centralizado_em(page, fundo, "Validar certificado", rect.y1 + 14, 7.2)
        _texto_centralizado_em(page, fundo, f"Código: {codigo}", rect.y1 + 26, 6.8)


def campos_faltando(perfil: dict, dados: dict) -> list:
    """Campos do perfil sem dado correspondente — para a conferência da planilha."""
    faltando = []
    for campo in perfil.get("campos", []):
        chave = campo["campo"]
        if str(dados.get(chave) or "").strip() == "":
            faltando.append(chave)
    return faltando


def renderizar(perfil: dict, dados: dict, codigo: str, url_validacao: str) -> bytes:
    """Renderiza o certificado: PDF do perfil + campos sobrepostos + QR.

    ``dados`` é ``{"nome": ..., "cpf": ..., "tema": ..., "horas": ...}``. Chave
    ausente ou vazia deixa o espaço em branco do PDF intacto — a geração nunca
    para por causa de um campo faltando.
    """
    pdf = _resolver_pdf(perfil)
    try:
        doc = fitz.open(pdf)
    except Exception as exc:
        raise PerfilInvalido(
            f"Perfil '{perfil.get('id', '?')}': o PDF não pôde ser aberto ({type(exc).__name__})."
        )
    try:
        page = doc[0]
        for campo in perfil.get("campos", []):
            valor = str(dados.get(campo["campo"]) or "").strip()
            if valor:
                _desenhar_linha(page, campo, valor)

        qr = perfil.get("qr")
        if qr and qr.get("mostrar", True):
            _desenhar_qr(page, qr, codigo, url_validacao)

        saida = io.BytesIO()
        doc.save(saida, garbage=4, deflate=True)
        return saida.getvalue()
    finally:
        doc.close()
