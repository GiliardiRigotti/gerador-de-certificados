"""Envio de certificados por e-mail.

Isola o SMTP da interface Streamlit. Nenhuma funcao aqui levanta excecao para a UI:
falha de e-mail nunca pode abortar a geracao de certificados.
"""

import html
import re
import smtplib
import ssl
import unicodedata
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

import pandas as pd

from config import (
    EMAIL_ASSUNTO,
    SMTP_FROM_EMAIL,
    SMTP_FROM_NAME,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_TIMEOUT,
    SMTP_USE_SSL,
    SMTP_USER,
)


EMAIL_REGEX = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}$")

NOME_ALIASES = {"nome", "name", "participante", "nomecompleto", "nomedoparticipante"}
EMAIL_ALIASES = {"email", "mail", "correio", "emaildoparticipante", "enderecodeemail"}

# Colunas opcionais usadas pelos perfis de certificado (ex.: palestrante).
# Ausentes = campo vazio, nunca erro. Chave interna -> nomes aceitos (sem acento).
COLUNAS_EXTRAS = {
    "cpf": {"cpf", "documento", "doc", "cpfdopalestrante"},
    "tema": {"tema", "temapalestra", "temadapalestra", "titulo", "assunto", "palestra"},
    "horas": {
        "horas",
        "horaspalestra",
        "horasdapalestra",
        "cargahorariapalestra",
        "cargahorariadapalestra",
        "duracao",
    },
}

ERRO_SEM_CONFIG = "SMTP nao configurado"
ERRO_EMAIL_INVALIDO = "endereco invalido"
ERRO_DESATIVADO = "envio desativado"
ERRO_SERVIDOR_INACESSIVEL = "servidor SMTP inacessivel, envio interrompido"

# O servidor respondeu recusando esta mensagem: reenviar so duplicaria.
ERROS_PERMANENTES = (
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPSenderRefused,
    smtplib.SMTPDataError,
    smtplib.SMTPNotSupportedError,
)
# A conexao caiu antes de uma resposta: vale reconectar e tentar de novo.
ERROS_DE_CONEXAO = (
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPConnectError,
    ConnectionError,
    TimeoutError,
    OSError,
)


def _normalizar(texto) -> str:
    """Minusculas, sem acento e sem separadores. 'E-mail ' -> 'email'."""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[\s_\-.]", "", texto).strip().lower()


def smtp_configurado() -> bool:
    return bool(SMTP_HOST and SMTP_FROM_EMAIL)


def email_valido(valor: str) -> bool:
    return bool(EMAIL_REGEX.match((valor or "").strip()))


def _mensagem_segura(exc: Exception) -> str:
    """Texto de erro sem credencial. Vai para a tela e para o banco."""
    texto = f"{type(exc).__name__}: {exc}"
    for segredo in (SMTP_PASSWORD, SMTP_USER):
        if segredo and len(segredo) > 2:
            texto = texto.replace(segredo, "***")
    texto = " ".join(texto.split())
    return texto[:200]


def _nome_arquivo_padrao(nome: str) -> str:
    limpo = re.sub(r"[^A-Za-zÀ-ÿ0-9 _-]", "", nome or "").strip()
    limpo = re.sub(r"\s+", "_", limpo)[:90]
    return f"certificado_{limpo or 'certificado'}.pdf"


def _mapear_colunas(colunas) -> dict:
    """Casa os cabeçalhos da planilha com as chaves internas.

    Devolve ``{"nome": <col>, "email": <col>, "cpf": <col>, ...}`` apenas para as
    chaves encontradas. A primeira coluna que casa vence.
    """
    achado = {}
    for coluna in colunas:
        chave = _normalizar(coluna)
        if "nome" not in achado and chave in NOME_ALIASES:
            achado["nome"] = coluna
        elif "email" not in achado and chave in EMAIL_ALIASES:
            achado["email"] = coluna
        else:
            for interna, aliases in COLUNAS_EXTRAS.items():
                if interna not in achado and chave in aliases:
                    achado[interna] = coluna
                    break
    return achado


def _localizar_colunas(colunas):
    mapa = _mapear_colunas(colunas)
    return mapa.get("nome"), mapa.get("email")


def _ler_csv(arquivo, encoding: str, separador: str) -> pd.DataFrame:
    if hasattr(arquivo, "seek"):
        arquivo.seek(0)
    # index_col=False é obrigatorio: sem ele, uma linha com separador sobrando
    # ("Maria;maria@ex.com;") faz o pandas promover a primeira coluna a indice
    # e o e-mail passa a ocupar a coluna do nome.
    return pd.read_csv(
        arquivo,
        sep=separador,
        engine="python",
        dtype=str,
        index_col=False,
        encoding=encoding,
    )


def _ler_planilha(arquivo) -> pd.DataFrame:
    nome_arquivo = (getattr(arquivo, "name", "") or "").lower()
    if hasattr(arquivo, "seek"):
        arquivo.seek(0)

    # .xls binario antigo exigiria xlrd; so xlsx e suportado.
    if nome_arquivo.endswith(".xlsx"):
        return pd.read_excel(arquivo, dtype=str)

    # Separadores explicitos em vez de sep=None: o csv.Sniffer chuta letras
    # como delimitador em arquivos de uma coluna ('nome' -> colunas 'n' e 'me').
    ultimo_erro = None
    for encoding in ("utf-8-sig", "cp1252"):
        for separador in (";", ",", "\t"):
            try:
                df = _ler_csv(arquivo, encoding, separador)
            except (UnicodeDecodeError, pd.errors.ParserError) as exc:
                ultimo_erro = exc
                continue
            if len(df.columns) >= 2:
                return df
            ultimo_erro = ValueError("a planilha tem uma coluna apenas")
    raise ValueError(
        "Nao foi possivel separar as colunas da planilha. "
        "Use um arquivo com as colunas 'nome' e 'email' separadas por ; ou ,."
    ) from ultimo_erro


def _celula(valor) -> str:
    return "" if pd.isna(valor) else str(valor).strip()


def parse_planilha(arquivo) -> dict:
    """Le CSV/XLSX e devolve ``{nome: {"email", "cpf", "tema", "horas"}}``.

    Preserva a ordem do arquivo. Nomes repetidos mantem a primeira ocorrencia,
    igual ao dedupe de split_names. So 'nome' e 'email' sao obrigatorios; as
    colunas extras ausentes viram string vazia — nao e erro.
    Levanta ValueError apenas quando o arquivo e inutilizavel.
    """
    try:
        df = _ler_planilha(arquivo)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Nao foi possivel ler a planilha ({_mensagem_segura(exc)}).")

    if df.empty:
        raise ValueError("A planilha esta vazia.")

    mapa = _mapear_colunas(df.columns)
    if "nome" not in mapa:
        raise ValueError("A planilha precisa de uma coluna 'nome'.")
    if "email" not in mapa:
        raise ValueError("A planilha precisa de uma coluna 'email'.")

    extras = [chave for chave in COLUNAS_EXTRAS if chave in mapa]
    participantes = {}
    vistos = set()
    for _, linha in df.iterrows():
        nome = _celula(linha[mapa["nome"]])
        if not nome:
            continue
        chave = nome.lower()
        if chave in vistos:
            continue
        vistos.add(chave)
        registro = {"email": _celula(linha[mapa["email"]])}
        for extra in COLUNAS_EXTRAS:
            registro[extra] = _celula(linha[mapa[extra]]) if extra in extras else ""
        participantes[nome] = registro

    if not participantes:
        raise ValueError("Nenhum nome valido encontrado na planilha.")

    # Rede de seguranca: nome que e um endereco de e-mail significa colunas
    # desalinhadas. Sem isso o certificado sai impresso com o e-mail no lugar do nome.
    if any(email_valido(nome) for nome in participantes):
        raise ValueError(
            "As colunas parecem trocadas: ha e-mail na coluna de nome. "
            "Confira o cabecalho e os separadores da planilha."
        )
    return participantes


def parse_planilha_destinatarios(arquivo) -> dict:
    """Compat: ``{nome: email}``. Usa parse_planilha por baixo."""
    return {nome: dados["email"] for nome, dados in parse_planilha(arquivo).items()}


def montar_mensagem(destino: str, nome: str, codigo: str, pdf_bytes: bytes, url_validacao: str, evento: str = "", nome_arquivo: str = None) -> EmailMessage:
    assunto = (EMAIL_ASSUNTO or "Seu certificado").replace("{evento}", evento or "").replace("{nome}", nome or "")

    msg = EmailMessage()
    msg["Subject"] = " ".join(assunto.split())
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM_EMAIL))
    # Um destinatario por mensagem: nunca expor a lista de participantes.
    # strip() aqui tambem: validador e consumidor precisam ver o mesmo valor,
    # senao um endereco com \n vira injecao de cabecalho.
    msg["To"] = (destino or "").strip()
    # Date e Message-ID nao sao gerados pelo EmailMessage nem pelo send_message.
    # Sem eles a mensagem viola a RFC 5322 e o filtro de spam pontua contra.
    msg["Date"] = formatdate(localtime=True)
    dominio = SMTP_FROM_EMAIL.split("@")[-1] if "@" in SMTP_FROM_EMAIL else None
    msg["Message-ID"] = make_msgid(domain=dominio)

    corpo_evento = f" da {evento}" if evento else ""
    texto = (
        f"Olá, {nome}!\n\n"
        f"Segue em anexo o seu certificado{corpo_evento}.\n\n"
        f"Código de autenticação: {codigo}\n"
        f"Valide a autenticidade em: {url_validacao}\n\n"
        "Esta é uma mensagem automática. Guarde este e-mail para consultas futuras.\n"
    )
    msg.set_content(texto)
    # Nome e evento vem de planilha enviada pelo operador: escapar antes de virar HTML.
    nome_html = html.escape(nome or "")
    codigo_html = html.escape(codigo or "")
    url_html = html.escape(url_validacao or "", quote=True)
    evento_html = html.escape(corpo_evento)
    msg.add_alternative(
        f"""<html><body style="font-family:Arial,Helvetica,sans-serif;color:#243b53;">
<p>Olá, <strong>{nome_html}</strong>!</p>
<p>Segue em anexo o seu certificado{evento_html}.</p>
<p>Código de autenticação: <strong>{codigo_html}</strong><br>
Valide a autenticidade em: <a href="{url_html}">{url_html}</a></p>
<p style="color:#52616f;font-size:0.9em;">Esta é uma mensagem automática.
Guarde este e-mail para consultas futuras.</p>
</body></html>""",
        subtype="html",
    )
    msg.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=nome_arquivo or _nome_arquivo_padrao(nome),
    )
    return msg


class EnviadorLote:
    """Mantem uma unica conexao SMTP para o lote inteiro.

    Uso:
        with EnviadorLote(ativo=True) as enviador:
            ok, erro = enviador.enviar(...)   # nunca levanta

    Reconecta uma vez quando a conexao cai no meio do lote.
    """

    MAX_FALHAS_CONEXAO = 3

    def __init__(self, ativo: bool = True):
        self.ativo = ativo
        self._conexao = None
        self._erro_conexao = None
        self._falhas_conexao = 0

    def __enter__(self):
        # Conexao preguicosa: so abre no primeiro envio realmente valido. Um lote
        # inteiro de enderecos invalidos nao deve tocar a rede.
        return self

    def __exit__(self, *_):
        self.fechar()
        return False

    def _conectar(self):
        try:
            contexto = ssl.create_default_context()
            if SMTP_USE_SSL:
                conexao = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT, context=contexto)
            else:
                conexao = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT)
                conexao.starttls(context=contexto)
            if SMTP_USER:
                conexao.login(SMTP_USER, SMTP_PASSWORD)
            self._conexao = conexao
            self._erro_conexao = None
            self._falhas_conexao = 0
        except Exception as exc:
            self._conexao = None
            self._erro_conexao = _mensagem_segura(exc)
            self._falhas_conexao += 1

    def fechar(self):
        if self._conexao is not None:
            try:
                self._conexao.quit()
            except Exception:
                pass
            self._conexao = None

    def enviar(self, destino: str, nome: str, codigo: str, pdf_bytes: bytes, url_validacao: str, evento: str = "", nome_arquivo: str = None):
        """Devolve (ok, erro). Nunca levanta excecao."""
        if not self.ativo:
            return False, ERRO_DESATIVADO
        if not smtp_configurado():
            return False, ERRO_SEM_CONFIG
        if not email_valido(destino):
            return False, ERRO_EMAIL_INVALIDO

        try:
            mensagem = montar_mensagem(destino, nome, codigo, pdf_bytes, url_validacao, evento, nome_arquivo)
        except Exception as exc:
            return False, _mensagem_segura(exc)

        # Host morto: parar de tentar. Sem isso um servidor inalcancavel custa
        # um timeout inteiro por destinatario e trava o Streamlit por horas.
        if self._falhas_conexao >= self.MAX_FALHAS_CONEXAO:
            return False, f"{ERRO_SERVIDOR_INACESSIVEL}: {self._erro_conexao}"

        for tentativa in (1, 2):
            if self._conexao is None:
                self._conectar()
            if self._conexao is None:
                return False, self._erro_conexao or ERRO_SEM_CONFIG
            try:
                self._conexao.send_message(mensagem)
                return True, None
            except ERROS_PERMANENTES as exc:
                # Recusa do servidor para esta mensagem. Reconectar nao ajuda e
                # reenviar pode duplicar uma mensagem que o servidor ja aceitou.
                return False, _mensagem_segura(exc)
            except ERROS_DE_CONEXAO as exc:
                erro = _mensagem_segura(exc)
                self.fechar()
                if tentativa == 2:
                    self._erro_conexao = erro
                    self._falhas_conexao += 1
                    return False, erro
            except Exception as exc:
                # Desconhecido: tratar como permanente para nao arriscar duplicata.
                return False, _mensagem_segura(exc)
        return False, ERRO_SEM_CONFIG
