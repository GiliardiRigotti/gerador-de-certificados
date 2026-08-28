import hashlib
import io
import logging
import os
import re
import secrets
import sqlite3
import string
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse

import fitz  # PyMuPDF
import pandas as pd
import qrcode
import streamlit as st

import perfil_certificado

from email_sender import (
    EnviadorLote,
    email_valido,
    parse_planilha,
    parse_planilha_destinatarios,
    smtp_configurado,
)

from config import (
    ADMIN_PASSWORD,
    ADMIN_PASSWORD_SHA256,
    ADMIN_USERNAME,
    DATABASE_PATH,
    EVENTO_PADRAO,
    OUTPUT_DIR,
    VALIDATION_BASE_URL,
)


st.set_page_config(page_title="Certificados com QR Code", page_icon="✅", layout="wide")

APP_DIR = Path(__file__).parent
DEFAULT_TEMPLATE = APP_DIR / "modelo_certificado.pdf"

# Log dedicado para diagnostico do login administrativo. Grava em arquivo na
# raiz do projeto e nao propaga para o root logger (evita poluir o stdout do
# Streamlit). Nao registra a senha em texto puro: apenas tamanho, um prefixo de
# hash e sinais de espacos/quebras de linha acidentais.
LOGIN_LOG_PATH = Path(os.getenv("LOGIN_LOG_PATH", APP_DIR / "login_debug.log"))
login_logger = logging.getLogger("certificados.login")
if not login_logger.handlers:
    login_logger.setLevel(logging.DEBUG)
    try:
        _login_handler = logging.FileHandler(LOGIN_LOG_PATH, encoding="utf-8")
    except OSError:
        _login_handler = logging.StreamHandler()
    _login_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    login_logger.addHandler(_login_handler)
    login_logger.propagate = False
    login_logger.info(
        "config carregada | ADMIN_USERNAME=%r | senha_configurada=%s | "
        "usa_sha256=%s | usa_plaintext=%s | ADMIN_PASSWORD_no_os_environ=%s | "
        "ADMIN_PASSWORD_SHA256_no_os_environ=%s | .env=%s (existe=%s)",
        ADMIN_USERNAME,
        bool(ADMIN_PASSWORD_SHA256 or ADMIN_PASSWORD),
        bool(ADMIN_PASSWORD_SHA256),
        bool(ADMIN_PASSWORD),
        "ADMIN_PASSWORD" in os.environ,
        "ADMIN_PASSWORD_SHA256" in os.environ,
        str(APP_DIR / ".env"),
        (APP_DIR / ".env").exists(),
    )

DEFAULT_EVENT_NAME = EVENTO_PADRAO
DEFAULT_EVENT_DATE = "26 e 27 de junho de 2026"
DEFAULT_WORKLOAD = "12 horas"
DEFAULT_LOCATION = "Centro de Eventos de Balneário Camboriú"
DEFAULT_CITY = "Balneário Camboriú"

DEFAULT_TEXT_PARTICIPACAO = (
    "participou da 9ª Conferência Municipal de Saúde de Balneário Camboriú,\n"
    "realizada nos dias 26 e 27 de junho de 2026, no Centro de Eventos de Balneário Camboriú,\n"
    "com carga horária total de 12 horas."
)

DEFAULT_TEXT_EQUIPE = (
    "atuou na equipe de trabalho da 9ª Conferência Municipal de Saúde de Balneário Camboriú,\n"
    "realizada nos dias 26 e 27 de junho de 2026, no Centro de Eventos de Balneário Camboriú,\n"
    "com carga horária total de 12 horas."
)

# Coordenadas ajustadas para o modelo enviado: certificado horizontal A4, 841.89 x 595.28 pt.
NAME_Y = 248
BODY_RECT = fitz.Rect(185, 284, 730, 342)
BODY_COVER_RECT = fitz.Rect(170, 278, 735, 348)
QR_RECT = fitz.Rect(705, 420, 775, 490)
NAVY = (0.02, 0.13, 0.34)
DARK = (0.05, 0.05, 0.05)
WHITE = (1, 1, 1)

CERTIFICATE_STATUSES = {"valido", "cancelado", "revogado"}

# Colunas de e-mail adicionadas depois da primeira versao do banco.
# Bancos existentes recebem estas colunas por ALTER TABLE em init_db().
EMAIL_COLUMNS = {
    "email": "TEXT",
    "email_status": "TEXT NOT NULL DEFAULT 'sem_email'",
    "email_enviado_em": "TEXT",
    "email_erro": "TEXT",
}

# Campos preenchidos pelos perfis de certificado (CPF/tema/horas da palestra) e o
# id do perfil usado. Tambem aditivos e idempotentes.
PERFIL_COLUMNS = {
    "cpf": "TEXT",
    "tema": "TEXT",
    "horas": "TEXT",
    "perfil": "TEXT",
}

COLUNAS_ADITIVAS = {**EMAIL_COLUMNS, **PERFIL_COLUMNS}

EMAIL_STATUS_SEM_EMAIL = "sem_email"
EMAIL_STATUS_NAO_ENVIADO = "nao_enviado"
EMAIL_STATUS_ENVIADO = "enviado"
EMAIL_STATUS_FALHOU = "falhou"


def get_connection():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS certificados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_unico TEXT NOT NULL UNIQUE,
                nome TEXT NOT NULL,
                evento TEXT NOT NULL,
                tipo_certificado TEXT NOT NULL,
                carga_horaria TEXT NOT NULL,
                data_evento TEXT NOT NULL,
                data_emissao TEXT NOT NULL,
                cidade TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'valido',
                arquivo_pdf TEXT,
                criado_em TEXT NOT NULL,
                atualizado_em TEXT NOT NULL,
                local TEXT,
                url_validacao TEXT,
                hash_pdf_sha256 TEXT,
                email TEXT,
                email_status TEXT NOT NULL DEFAULT 'sem_email',
                email_enviado_em TEXT,
                email_erro TEXT,
                cpf TEXT,
                tema TEXT,
                horas TEXT,
                perfil TEXT
            )
            """
        )
        existentes = {row["name"] for row in conn.execute("PRAGMA table_info(certificados)")}
        for coluna, ddl in COLUNAS_ADITIVAS.items():
            if coluna not in existentes:
                conn.execute(f"ALTER TABLE certificados ADD COLUMN {coluna} {ddl}")
        conn.commit()


def certificate_exists(code: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM certificados WHERE codigo_unico = ? LIMIT 1",
            (code,),
        ).fetchone()
    return row is not None


def insert_certificate(row: dict):
    fields = [
        "codigo_unico",
        "nome",
        "evento",
        "tipo_certificado",
        "carga_horaria",
        "data_evento",
        "data_emissao",
        "cidade",
        "status",
        "arquivo_pdf",
        "criado_em",
        "atualizado_em",
        "local",
        "url_validacao",
        "hash_pdf_sha256",
        "email",
        "email_status",
        "cpf",
        "tema",
        "horas",
        "perfil",
    ]
    placeholders = ", ".join(["?"] * len(fields))
    with get_connection() as conn:
        conn.execute(
            f"INSERT INTO certificados ({', '.join(fields)}) VALUES ({placeholders})",
            tuple(row.get(field, "") for field in fields),
        )
        conn.commit()


def find_certificate(code: str):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM certificados WHERE UPPER(codigo_unico) = UPPER(?)",
            ((code or "").strip(),),
        ).fetchone()
    return dict(row) if row else None


def search_certificates(term: str = "") -> pd.DataFrame:
    term = (term or "").strip()
    with get_connection() as conn:
        if term:
            rows = conn.execute(
                """
                SELECT * FROM certificados
                WHERE nome LIKE ? OR codigo_unico LIKE ?
                ORDER BY criado_em DESC, id DESC
                """,
                (f"%{term}%", f"%{term}%"),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM certificados ORDER BY criado_em DESC, id DESC"
            ).fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def update_certificate_status(code: str, status: str):
    status = status.lower().strip()
    if status not in CERTIFICATE_STATUSES:
        raise ValueError("Status inválido.")
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE certificados
            SET status = ?, atualizado_em = ?
            WHERE codigo_unico = ?
            """,
            (status, now, code),
        )
        conn.commit()


def set_certificate_email(code: str, email: str):
    """Grava so o endereco, sem mexer no status nem no historico de envio."""
    agora = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "UPDATE certificados SET email = ?, atualizado_em = ? WHERE codigo_unico = ?",
            ((email or "").strip(), agora, code),
        )
        conn.commit()


def update_certificate_email(code: str, email: str, status: str, erro: str = None):
    """Grava o resultado do envio.

    Uma tentativa que falha NUNCA apaga o endereco que ja funcionou nem a data
    de uma entrega anterior: um reenvio para endereco digitado errado nao pode
    destruir a prova de que o certificado foi entregue.
    """
    agora = datetime.now().isoformat(timespec="seconds")
    email = (email or "").strip()
    with get_connection() as conn:
        if status == EMAIL_STATUS_ENVIADO:
            conn.execute(
                """
                UPDATE certificados
                SET email = ?, email_status = ?, email_erro = NULL,
                    email_enviado_em = ?, atualizado_em = ?
                WHERE codigo_unico = ?
                """,
                (email, status, agora, agora, code),
            )
        else:
            # email_enviado_em fica de fora do SET: preserva a entrega anterior.
            # O endereco so e gravado quando ainda nao havia nenhum.
            conn.execute(
                """
                UPDATE certificados
                SET email = COALESCE(NULLIF(email, ''), ?), email_status = ?,
                    email_erro = ?, atualizado_em = ?
                WHERE codigo_unico = ?
                """,
                (email, status, erro, agora, code),
            )
        conn.commit()


def delete_certificate(code: str, delete_pdf: bool = False) -> bool:
    row = find_certificate(code)
    if not row:
        return False

    with get_connection() as conn:
        conn.execute("DELETE FROM certificados WHERE codigo_unico = ?", (row["codigo_unico"],))
        conn.commit()

    if delete_pdf and row.get("arquivo_pdf"):
        pdf_path = Path(row["arquivo_pdf"])
        try:
            output_dir = OUTPUT_DIR.resolve()
            resolved_pdf = pdf_path.resolve()
            if resolved_pdf.is_file() and output_dir in resolved_pdf.parents:
                resolved_pdf.unlink()
        except OSError:
            pass

    return True


def clean_filename(name: str) -> str:
    name = re.sub(r"[^A-Za-zÀ-ÿ0-9 _-]", "", name).strip()
    name = re.sub(r"\s+", "_", name)
    return name[:90] or "certificado"


def unique_pdf_name(name: str, code: str, usados: set) -> str:
    """Nome de arquivo unico dentro do lote.

    clean_filename colapsa nomes diferentes no mesmo resultado ('Ana  Souza' e
    'Ana Souza'), e devolve 'certificado' para nomes sem caracteres latinos
    (chines, grego, arabe). Sem desambiguar, um PDF sobrescreve o outro e o
    reenvio entrega a pessoa errada.
    """
    base = clean_filename(name)
    if base == "certificado":
        base = f"certificado_{code}"
    nome_arquivo = f"certificado_{base}.pdf"
    if nome_arquivo in usados:
        nome_arquivo = f"certificado_{base}_{code}.pdf"
    usados.add(nome_arquivo)
    return nome_arquivo


def split_names(raw: str):
    items = []
    for line in raw.replace(";", "\n").splitlines():
        chunks = [c.strip() for c in line.split(",") if c.strip()]
        if len(chunks) > 1:
            items.extend(chunks)
        elif line.strip():
            items.append(line.strip())
    seen = set()
    result = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


# Partículas que ficam em minúsculo no meio de um nome (nunca no início).
PARTICULAS_NOME = {
    "da", "de", "do", "das", "dos", "e", "di", "du", "del", "della",
    "la", "le", "van", "von", "y",
}


def _titlecase_palavra(palavra: str) -> str:
    """'JOSÉ' -> 'José', 'ana-beatriz' -> 'Ana-Beatriz', "d'ávila" -> "D'Ávila"."""
    for sep in ("-", "'", "’"):
        if sep in palavra:
            return sep.join(_titlecase_palavra(p) for p in palavra.split(sep))
    return palavra[:1].upper() + palavra[1:].lower() if palavra else palavra


def normalizar_nome_proprio(nome: str) -> str:
    """Conserta a caixa de nomes bagunçados sem tocar nos que já estão certos.

    'VERA SUSANA La Falc'      -> 'Vera Susana La Falc'
    'William Xavier Oliveira'  -> 'William Xavier Oliveira'  (inalterado)
    'maria da silva'           -> 'Maria da Silva'

    Cada palavra só é ajustada quando está TODA em maiúsculas ou TODA em
    minúsculas; palavras com caixa mista ('La', 'McCarthy') são preservadas.
    Partículas (da, de, von...) ficam minúsculas, exceto no início do nome.
    """
    nome = " ".join((nome or "").split())
    if not nome:
        return nome

    palavras = nome.split(" ")
    resultado = []
    for i, palavra in enumerate(palavras):
        so_letras = re.sub(r"[-'’]", "", palavra)
        if so_letras and not (so_letras.isupper() or so_letras.islower()):
            resultado.append(palavra)  # caixa mista: já veio ajustado
        elif i > 0 and palavra.lower() in PARTICULAS_NOME:
            resultado.append(palavra.lower())
        else:
            resultado.append(_titlecase_palavra(palavra))
    return " ".join(resultado)


def normalize_base_url(url: str) -> str:
    url = (url or "").strip() or "http://localhost:8501/"
    url = url.rstrip("/")
    # Sem path o resultado sairia como "https://host?codigo=", que e valido mas
    # depende do cliente normalizar. Em QR Code impresso nao ha segunda chance.
    if not urlparse(url).path:
        url += "/"
    return url


def make_code(prefix: str = "CMS2026") -> str:
    safe_prefix = re.sub(r"[^A-Za-z0-9]", "", prefix.upper())[:12] or "CERT2026"
    alphabet = string.ascii_uppercase + string.digits
    while True:
        token = "".join(secrets.choice(alphabet) for _ in range(6))
        code = f"{safe_prefix}-{token}"
        if not certificate_exists(code):
            return code


def build_validation_url(code: str) -> str:
    return f"{normalize_base_url(VALIDATION_BASE_URL)}?{urlencode({'codigo': code})}"


def make_qr_png_bytes(data: str) -> bytes:
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


def insert_centered_text(page, text, y, max_width=420, fontname="helv", base_size=25, min_size=14, color=NAVY):
    text = text.strip().upper()
    fontsize = base_size
    while fontsize > min_size and fitz.get_text_length(text, fontname=fontname, fontsize=fontsize) > max_width:
        fontsize -= 1
    text_width = fitz.get_text_length(text, fontname=fontname, fontsize=fontsize)
    x = (page.rect.width - text_width) / 2
    page.insert_text((x, y), text, fontsize=fontsize, fontname=fontname, color=color)


def insert_centered_text_in_rect(page, rect, text, y, base_size=7, min_size=5.5, fontname="helv", color=DARK):
    fontsize = base_size
    while fontsize > min_size and fitz.get_text_length(text, fontname=fontname, fontsize=fontsize) > rect.width - 8:
        fontsize -= 0.25
    text_width = fitz.get_text_length(text, fontname=fontname, fontsize=fontsize)
    x = rect.x0 + (rect.width - text_width) / 2
    page.insert_text((x, y), text, fontsize=fontsize, fontname=fontname, color=color)


def make_certificate(template_bytes: bytes, person_name: str, body_text: str, replace_body: bool, code: str, validation_url: str, show_qr: bool = True) -> bytes:
    doc = fitz.open(stream=template_bytes, filetype="pdf")
    page = doc[0]

    insert_centered_text(page, person_name, NAME_Y)

    if replace_body:
        page.draw_rect(BODY_COVER_RECT, color=WHITE, fill=WHITE, overlay=True)
        fontsize = 10.5
        while fontsize >= 8:
            result = page.insert_textbox(
                BODY_RECT,
                body_text,
                fontsize=fontsize,
                fontname="helv",
                color=NAVY,
                align=fitz.TEXT_ALIGN_CENTER,
            )
            if result >= 0:
                break
            fontsize -= 0.5

    if show_qr:
        bg = fitz.Rect(QR_RECT.x0 - 20, QR_RECT.y0 - 8, QR_RECT.x1 + 20, QR_RECT.y1 + 32)
        page.draw_rect(bg, color=WHITE, fill=WHITE, overlay=True, radius=0.15)
        qr_bytes = make_qr_png_bytes(validation_url)
        page.insert_image(QR_RECT, stream=qr_bytes, overlay=True)
        insert_centered_text_in_rect(page, bg, "Validar certificado", 504, base_size=7.2)
        insert_centered_text_in_rect(page, bg, f"Código: {code}", 516, base_size=6.8)

    output = io.BytesIO()
    doc.save(output, garbage=4, deflate=True)
    doc.close()
    return output.getvalue()


def dataframe_to_csv_bytes(df: pd.DataFrame):
    return df.to_csv(index=False).encode("utf-8-sig")


def certificado_legado(template_bytes, body_text, replace_body):
    """render_fn para o modelo atual da 9ª Conferência (upload + parágrafo)."""

    def _render(name, _dados, code, validation_url):
        return make_certificate(
            template_bytes, name, body_text, replace_body, code, validation_url, show_qr=True
        )

    return _render


def certificado_por_perfil(perfil):
    """render_fn que desenha a partir de um perfil JSON e dos dados do participante."""

    def _render(name, dados, code, validation_url):
        return perfil_certificado.renderizar(
            perfil,
            {
                "nome": name,
                "cpf": dados.get("cpf", ""),
                "tema": dados.get("tema", ""),
                "horas": dados.get("horas", ""),
            },
            code,
            validation_url,
        )

    return _render


def make_zip(names, participantes, render_fn, prefix: str, event_name: str, cert_type: str, event_date: str, workload: str, location: str, city: str, perfil_id: str = "", enviar_emails: bool = False, progress_callback=None):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    zip_buffer = io.BytesIO()
    merged = fitz.open()
    registry_rows = []
    generated_at = datetime.now().isoformat(timespec="seconds")

    dados_lookup = {
        str(k).strip().lower(): (v or {}) for k, v in (participantes or {}).items()
    }
    resumo = {"enviados": 0, "falhas": 0, "sem_email": 0, "erros": []}
    total = len(names)
    nomes_de_arquivo = set()

    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        with EnviadorLote(ativo=enviar_emails) as enviador:
            for indice, name in enumerate(names, start=1):
                code = make_code(prefix)
                validation_url = build_validation_url(code)
                dados = dados_lookup.get(name.strip().lower(), {})
                pdf_bytes = render_fn(name, dados, code, validation_url)
                pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
                pdf_name = unique_pdf_name(name, code, nomes_de_arquivo)
                pdf_path = OUTPUT_DIR / pdf_name
                pdf_path.write_bytes(pdf_bytes)

                email = (dados.get("email") or "").strip()
                email_status = EMAIL_STATUS_SEM_EMAIL if not email else EMAIL_STATUS_NAO_ENVIADO

                row = {
                    "codigo_unico": code,
                    "nome": name,
                    "evento": event_name,
                    "tipo_certificado": cert_type,
                    "carga_horaria": workload,
                    "data_evento": event_date,
                    "data_emissao": generated_at,
                    "cidade": city,
                    "status": "valido",
                    "arquivo_pdf": str(pdf_path),
                    "criado_em": generated_at,
                    "atualizado_em": generated_at,
                    "local": location,
                    "url_validacao": validation_url,
                    "hash_pdf_sha256": pdf_hash,
                    "email": email,
                    "email_status": email_status,
                    "email_erro": None,
                    "cpf": (dados.get("cpf") or "").strip(),
                    "tema": (dados.get("tema") or "").strip(),
                    "horas": (dados.get("horas") or "").strip(),
                    "perfil": perfil_id,
                }
                insert_certificate(row)

                # A partir daqui o certificado ja esta salvo: nenhuma falha de e-mail
                # pode impedir a entrega do PDF, do registro ou do ZIP.
                if not email:
                    resumo["sem_email"] += 1
                elif enviar_emails:
                    ok, erro = enviador.enviar(
                        destino=email,
                        nome=name,
                        codigo=code,
                        pdf_bytes=pdf_bytes,
                        url_validacao=validation_url,
                        evento=event_name,
                        nome_arquivo=pdf_name,
                    )
                    row["email_status"] = EMAIL_STATUS_ENVIADO if ok else EMAIL_STATUS_FALHOU
                    row["email_erro"] = None if ok else erro
                    try:
                        update_certificate_email(code, email, row["email_status"], row["email_erro"])
                    except sqlite3.Error as exc:
                        # O certificado ja esta emitido. Um banco travado nao pode
                        # derrubar a geracao so por causa do status do e-mail.
                        resumo["erros"].append(f"{name}: status de e-mail nao gravado ({exc})")
                    if ok:
                        resumo["enviados"] += 1
                    else:
                        resumo["falhas"] += 1
                        resumo["erros"].append(f"{name} <{email}>: {erro}")

                registry_rows.append(row)

                zf.writestr(pdf_name, pdf_bytes)
                single = fitz.open(stream=pdf_bytes, filetype="pdf")
                merged.insert_pdf(single)
                single.close()

                if progress_callback:
                    progress_callback(indice, total, name)

        merged_bytes = io.BytesIO()
        merged.save(merged_bytes, garbage=4, deflate=True)
        merged.close()
        zf.writestr("TODOS_OS_CERTIFICADOS.pdf", merged_bytes.getvalue())
        zf.writestr("certificados_emitidos.csv", dataframe_to_csv_bytes(pd.DataFrame(registry_rows)))
        zf.writestr("LEIA-ME-validacao.txt", make_validation_readme().encode("utf-8"))

    zip_buffer.seek(0)
    return zip_buffer.getvalue(), registry_rows, resumo


def make_validation_readme() -> str:
    return f"""VALIDAÇÃO DOS CERTIFICADOS

Cada certificado possui um QR Code único. O QR abre a página pública:
{normalize_base_url(VALIDATION_BASE_URL)}?codigo=CODIGO_DO_CERTIFICADO

O organizador não precisa digitar a URL na tela. Altere VALIDATION_BASE_URL no config.py
ou configure a variável de ambiente VALIDATION_BASE_URL no servidor oficial.
"""


def admin_password_configurada() -> bool:
    return bool(ADMIN_PASSWORD_SHA256 or ADMIN_PASSWORD)


def _fingerprint(value: str) -> str:
    """Prefixo do SHA-256: compara valores no log sem expor o texto puro."""
    if value is None:
        return "<none>"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def log_login_attempt(username: str, password: str, sucesso: bool) -> None:
    if ADMIN_PASSWORD_SHA256:
        metodo = "sha256"
        senha_esperada_fp = ADMIN_PASSWORD_SHA256[:12]
    elif ADMIN_PASSWORD:
        metodo = "plaintext"
        senha_esperada_fp = _fingerprint(ADMIN_PASSWORD)
    else:
        metodo = "nenhum"
        senha_esperada_fp = "<none>"
    login_logger.debug(
        "login | resultado=%s | "
        "user_recebido=%r len=%d | user_esperado=%r len=%d | user_match=%s | "
        "senha_configurada=%s metodo=%s | "
        "ADMIN_PASSWORD_no_os_environ=%s ADMIN_PASSWORD_SHA256_no_os_environ=%s | "
        "senha_recebida len=%d fp=%s | senha_esperada fp=%s | "
        "senha_tem_espaco_nas_pontas=%s | senha_match=%s",
        "OK" if sucesso else "FALHOU",
        username, len(username), ADMIN_USERNAME, len(ADMIN_USERNAME),
        username == ADMIN_USERNAME,
        admin_password_configurada(), metodo,
        "ADMIN_PASSWORD" in os.environ, "ADMIN_PASSWORD_SHA256" in os.environ,
        len(password), _fingerprint(password), senha_esperada_fp,
        password != password.strip(),
        check_password(password),
    )


def check_password(password: str) -> bool:
    if not admin_password_configurada():
        return False
    if ADMIN_PASSWORD_SHA256:
        digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return secrets.compare_digest(digest, ADMIN_PASSWORD_SHA256)
    return secrets.compare_digest(password, ADMIN_PASSWORD)


def render_login():
    st.markdown(
        """
        <style>
            .stApp {
                background:
                    radial-gradient(circle at top left, rgba(28, 78, 216, 0.12), transparent 32rem),
                    linear-gradient(135deg, #f7f9fc 0%, #edf2f7 100%);
            }

            .block-container {
                max-width: 460px;
                padding-top: 11vh;
                padding-bottom: 2rem;
            }

            .login-brand {
                text-align: center;
                margin-bottom: 1.25rem;
            }

            .login-brand h1 {
                color: #102a43;
                font-size: 2rem;
                line-height: 1.15;
                font-weight: 750;
                letter-spacing: 0;
                margin: 0 0 0.45rem;
            }

            .login-brand p {
                color: #52616f;
                font-size: 0.98rem;
                margin: 0;
            }

            div[data-testid="stForm"] {
                background: rgba(255, 255, 255, 0.94);
                border: 1px solid rgba(148, 163, 184, 0.25);
                border-radius: 14px;
                box-shadow: 0 18px 45px rgba(15, 23, 42, 0.10);
                padding: 1.35rem 1.35rem 1.15rem;
            }

            div[data-testid="stTextInput"] label {
                color: #243b53;
                font-weight: 650;
            }

            div[data-testid="stTextInput"] input {
                border-radius: 10px;
            }

            div[data-testid="stFormSubmitButton"] button {
                width: 100%;
                border-radius: 10px;
                font-weight: 700;
            }

            div[data-testid="stAlert"] {
                border-radius: 12px;
                margin-top: 1rem;
            }
        </style>
        <div class="login-brand">
            <h1>Sistema Municipal de Certificados</h1>
            <p>Acesso interno para gestão dos certificados do evento.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.form("admin_login"):
        username = st.text_input("Usuário", placeholder="Digite seu usuário")
        password = st.text_input("Senha", type="password")
        submitted = st.form_submit_button("Entrar", type="primary")
    if not admin_password_configurada():
        st.error(
            "Nenhuma senha administrativa configurada. Defina ADMIN_PASSWORD "
            "(ou ADMIN_PASSWORD_SHA256) no .env ou no ambiente do servidor."
        )
    if submitted:
        ok = secrets.compare_digest(username, ADMIN_USERNAME) and check_password(password)
        log_login_attempt(username, password, ok)
        if ok:
            st.session_state["admin_authenticated"] = True
            st.rerun()
        else:
            st.error("Usuário ou senha inválidos.")


def campos_publicos_extra(row: dict) -> list:
    """Pares (rótulo, valor) extras exibidos na consulta pública de palestrante.

    CPF nunca entra aqui: dado pessoal não aparece em página pública.
    """
    extra = []
    tema = (row.get("tema") or "").strip()
    horas = (row.get("horas") or "").strip()
    if tema:
        extra.append(("Tema da palestra", tema))
    if horas:
        extra.append(("Carga horária da palestra", horas))
    return extra


def render_public_validation(code: str = ""):
    st.title("Validação de certificado")
    st.caption("Consulta pública de autenticidade.")
    code = st.text_input("Código de autenticação", value=(code or "").strip().upper(), placeholder="Ex.: CMS2026-A8F3K2")
    if st.button("Consultar certificado", type="primary") or code:
        row = find_certificate(code)
        if not row:
            st.error("Certificado não encontrado.")
            st.write("Confira o código informado ou entre em contato com a organização do evento.")
            return
        if row["status"] != "valido":
            st.warning("Certificado inválido ou revogado.")
            st.write(f"Código consultado: **{row['codigo_unico']}**")
            st.write("Este registro não está válido para comprovação institucional.")
            return

        st.success("Certificado válido.")
        col1, col2 = st.columns(2)
        with col1:
            st.write("**Nome da pessoa:**", row["nome"])
            st.write("**Nome do evento:**", row["evento"])
            st.write("**Tipo de certificado:**", row["tipo_certificado"])
            st.write("**Carga horária:**", row["carga_horaria"])
        with col2:
            st.write("**Data do evento:**", row["data_evento"])
            st.write("**Data de emissão:**", row["data_emissao"])
            st.write("**Cidade:**", row["cidade"])
            st.write("**Código de autenticação:**", row["codigo_unico"])

        for rotulo, valor in campos_publicos_extra(row):
            st.write(f"**{rotulo}:**", valor)


LEGADO_ID = "__legado__"
LEGADO_ROTULO = "9ª Conferência de Saúde (modelo atual)"


def _checkbox_envio(tem_email: bool) -> bool:
    smtp_ok = smtp_configurado()
    enviar = st.checkbox(
        "Enviar certificados por e-mail ao gerar",
        value=smtp_ok and tem_email,
        disabled=not smtp_ok,
        help="Cada participante recebe o PDF em anexo e o link de validação.",
    )
    if not smtp_ok:
        st.caption("Envio de e-mail desativado: defina SMTP_HOST e SMTP_USER (ou SMTP_FROM_EMAIL) no servidor.")
    elif enviar and not tem_email:
        st.warning("Envie a planilha com a coluna de e-mail para que o disparo aconteça.")
    return enviar


def _aviso_regeracao():
    if st.session_state.get("ultimo_lote"):
        st.caption(
            "Atenção: clicar em Gerar novamente emite certificados NOVOS, com novos "
            "códigos, e reenvia os e-mails. Para apenas rebaixar o ZIP, use o botão abaixo."
        )


def _executar_geracao(names, participantes, render_fn, *, prefix, event_name, cert_type,
                      event_date, workload, location, city, perfil_id, enviar_emails):
    # Pré-voo: renderiza um certificado descartável antes de tocar banco ou disco.
    # Um modelo que não renderiza falha aqui, sem deixar lote pela metade.
    try:
        render_fn("verificação", {}, "PREVOO-000000", build_validation_url("PREVOO-000000"))
    except Exception as exc:
        st.error(f"O modelo do certificado não pôde ser renderizado: {exc}")
        return

    barra = st.progress(0.0, text="Gerando certificados...")

    def progresso(indice, total, nome):
        rotulo = "Gerando e enviando" if enviar_emails else "Gerando"
        barra.progress(indice / total, text=f"{rotulo} {indice}/{total} — {nome}")

    try:
        zip_bytes, rows, resumo = make_zip(
            names, participantes, render_fn, prefix, event_name, cert_type,
            event_date, workload, location, city, perfil_id=perfil_id,
            enviar_emails=enviar_emails, progress_callback=progresso,
        )
    finally:
        barra.empty()

    # Guardar em sessao: o resultado precisa sobreviver aos reruns do Streamlit.
    # Se o ZIP sumisse a cada interacao, o operador clicaria em Gerar de novo e
    # emitiria certificados e e-mails duplicados.
    st.session_state["ultimo_lote"] = {
        "zip": zip_bytes, "rows": rows, "resumo": resumo, "enviou": enviar_emails,
    }


def render_generate_tab():
    perfis = perfil_certificado.listar_perfis()
    opcoes = [LEGADO_ID] + [p["id"] for p in perfis]
    rotulos = {LEGADO_ID: LEGADO_ROTULO, **{p["id"]: p["nome"] for p in perfis}}
    escolha = st.selectbox("Modelo do certificado", opcoes, format_func=lambda o: rotulos[o])
    st.divider()

    if escolha == LEGADO_ID:
        _render_generate_legado()
    else:
        try:
            perfil = perfil_certificado.carregar_perfil(escolha)
        except perfil_certificado.PerfilInvalido as exc:
            st.error(str(exc))
            return
        _render_generate_perfil(perfil)

    render_ultimo_lote()


def _render_generate_legado():
    left, right = st.columns([1.1, 0.9])
    with left:
        uploaded_template = st.file_uploader("Modelo do certificado em PDF", type=["pdf"])
        planilha = st.file_uploader(
            "Planilha de participantes (opcional, com nome e e-mail)",
            type=["csv", "xlsx"],
            help="Colunas aceitas: nome e email. Se enviada, ela substitui a lista de nomes abaixo.",
        )
        names_raw = st.text_area(
            "Lista de nomes",
            height=180,
            placeholder="Cole um nome por linha. Exemplo:\nMaria da Silva\nJoão Pereira\nAna Souza",
        )
        tipo = st.radio(
            "Texto do certificado",
            ["Manter texto original e preencher só o nome", "Equipe de trabalho / organização", "Texto personalizado"],
        )
        replace_body = False
        body_text = DEFAULT_TEXT_PARTICIPACAO
        cert_type = "Participação"
        if tipo == "Equipe de trabalho / organização":
            replace_body = True
            cert_type = "Equipe de trabalho"
            body_text = st.text_area("Frase que substituirá o parágrafo central", DEFAULT_TEXT_EQUIPE, height=100)
        elif tipo == "Texto personalizado":
            replace_body = True
            cert_type = "Personalizado"
            body_text = st.text_area("Frase que substituirá o parágrafo central", DEFAULT_TEXT_PARTICIPACAO, height=100)

    with right:
        st.subheader("Dados do evento")
        prefix = st.text_input("Prefixo do código", value="CMS2026")
        event_name = st.text_input("Nome do evento", value=DEFAULT_EVENT_NAME)
        event_date = st.text_input("Data do evento", value=DEFAULT_EVENT_DATE)
        workload = st.text_input("Carga horária", value=DEFAULT_WORKLOAD)
        location = st.text_input("Local", value=DEFAULT_LOCATION)
        city = st.text_input("Cidade", value=DEFAULT_CITY)
        st.info("O QR Code usa a URL fixa do projeto e adiciona automaticamente o código único.")

    destinatarios = {}
    if planilha is not None:
        try:
            destinatarios = {
                normalizar_nome_proprio(nome): email
                for nome, email in parse_planilha_destinatarios(planilha).items()
            }
        except ValueError as exc:
            st.error(str(exc))
            # Planilha ilegivel nao pode cair calado na lista de nomes do textarea:
            # o operador acharia que os e-mails sairam.
            st.warning("Corrija a planilha e envie novamente. A geração está bloqueada até lá.")
            return

    if destinatarios:
        names = list(destinatarios)
        preview = pd.DataFrame(
            [
                {
                    "nome": nome,
                    "email": email or "(sem e-mail)",
                    "situação": "ok" if email_valido(email) else ("sem e-mail" if not email else "e-mail inválido"),
                }
                for nome, email in destinatarios.items()
            ]
        )
        validos = int((preview["situação"] == "ok").sum())
        st.info(f"{len(names)} nome(s) na planilha — {validos} com e-mail válido.")
        with st.expander("Conferir nomes e e-mails da planilha"):
            st.dataframe(preview, use_container_width=True, hide_index=True)
    else:
        names = [normalizar_nome_proprio(n) for n in split_names(names_raw)]
        if names:
            st.info(f"{len(names)} nome(s) identificado(s).")

    enviar_emails = _checkbox_envio(bool(destinatarios))
    _aviso_regeracao()

    if st.button("Gerar certificados com QR Code", type="primary"):
        try:
            if uploaded_template is not None:
                template_bytes = uploaded_template.read()
            elif DEFAULT_TEMPLATE.exists():
                template_bytes = DEFAULT_TEMPLATE.read_bytes()
            else:
                template_bytes = None

            if not template_bytes:
                st.error("Envie o PDF modelo do certificado.")
            elif not names:
                st.error("Cole pelo menos um nome na lista.")
            else:
                participantes = {nome: {"email": email} for nome, email in destinatarios.items()}
                _executar_geracao(
                    names,
                    participantes,
                    certificado_legado(template_bytes, body_text, replace_body),
                    prefix=prefix,
                    event_name=event_name,
                    cert_type=cert_type,
                    event_date=event_date,
                    workload=workload,
                    location=location,
                    city=city,
                    perfil_id="",
                    enviar_emails=enviar_emails,
                )
        except Exception as exc:
            st.exception(exc)


def _render_generate_perfil(perfil: dict):
    meta = perfil.get("metadados", {})
    obrig = perfil.get("colunas_obrigatorias") or []

    left, right = st.columns([1.1, 0.9])
    with left:
        if obrig:
            st.caption(
                "Este modelo exige planilha com as colunas: nome, email, "
                + ", ".join(obrig)
                + "."
            )
        planilha = st.file_uploader("Planilha de participantes (CSV ou XLSX)", type=["csv", "xlsx"])
        names_raw = ""
        if not obrig:
            names_raw = st.text_area(
                "Ou cole os nomes (um por linha)",
                height=140,
                placeholder="Maria da Silva\nJoão Pereira",
            )

    with right:
        st.subheader("Dados do evento")
        prefix = st.text_input("Prefixo do código", value=meta.get("prefixo", "CERT2026"))
        event_name = st.text_input("Nome do evento", value=meta.get("evento", ""))
        event_date = st.text_input("Data do evento", value=meta.get("data_evento", ""))
        workload = st.text_input("Carga horária", value=meta.get("carga_horaria", ""))
        location = st.text_input("Local", value=meta.get("local", ""))
        city = st.text_input("Cidade", value=meta.get("cidade", ""))
        st.info("O QR Code usa a URL fixa do projeto e adiciona automaticamente o código único.")

    cert_type = meta.get("tipo_certificado") or perfil["nome"]

    with st.expander("Pré-visualizar 1ª página"):
        exemplo = {
            "nome": "Fulana de Tal (exemplo)",
            "cpf": "000.000.000-00",
            "tema": "Título da palestra (exemplo)",
            "horas": "4",
        }
        try:
            amostra = perfil_certificado.renderizar(
                perfil, exemplo, "EXEMPLO-000000", build_validation_url("EXEMPLO-000000")
            )
            doc = fitz.open(stream=amostra, filetype="pdf")
            # width em pixels: compatível com todas as versões do Streamlit
            # (use_column_width / use_container_width mudam de nome entre versões).
            st.image(doc[0].get_pixmap(dpi=120).tobytes("png"), width=760)
            doc.close()
            st.caption(f"Ajuste as posições editando perfis/{perfil['id']}.json.")
        except perfil_certificado.PerfilInvalido as exc:
            st.error(str(exc))

    participantes = {}
    if planilha is not None:
        try:
            participantes = {
                normalizar_nome_proprio(nome): dados
                for nome, dados in parse_planilha(planilha).items()
            }
        except ValueError as exc:
            st.error(str(exc))
            st.warning("Corrija a planilha e envie novamente. A geração está bloqueada até lá.")
            return

    if participantes:
        names = list(participantes)
        linhas = []
        algum_faltando = False
        for nome, dados in participantes.items():
            faltas = perfil_certificado.campos_faltando(perfil, {"nome": nome, **dados})
            algum_faltando = algum_faltando or bool(faltas)
            email = dados.get("email", "")
            linhas.append(
                {
                    "nome": nome,
                    "email": email or "(sem e-mail)",
                    "situação e-mail": "ok" if email_valido(email) else ("sem e-mail" if not email else "inválido"),
                    "campos faltando": ", ".join(faltas) or "—",
                }
            )
        st.info(f"{len(names)} participante(s) na planilha.")
        with st.expander("Conferir participantes"):
            st.dataframe(pd.DataFrame(linhas), use_container_width=True, hide_index=True)
        if algum_faltando:
            st.warning(
                "Alguns certificados sairão com campos em branco — confira a coluna "
                "'campos faltando'."
            )
    elif obrig:
        st.info("Envie a planilha para continuar.")
        return
    else:
        names = [normalizar_nome_proprio(n) for n in split_names(names_raw)]
        participantes = {nome: {"email": ""} for nome in names}
        if names:
            st.info(f"{len(names)} nome(s) identificado(s).")

    if not names:
        return

    tem_email = any((dados.get("email") or "").strip() for dados in participantes.values())
    enviar_emails = _checkbox_envio(tem_email)
    _aviso_regeracao()

    if st.button("Gerar certificados com QR Code", type="primary"):
        try:
            _executar_geracao(
                names,
                participantes,
                certificado_por_perfil(perfil),
                prefix=prefix,
                event_name=event_name,
                cert_type=cert_type,
                event_date=event_date,
                workload=workload,
                location=location,
                city=city,
                perfil_id=perfil["id"],
                enviar_emails=enviar_emails,
            )
        except Exception as exc:
            st.exception(exc)


def resend_certificate_email(code: str, email_informado: str = ""):
    """Reenvia o certificado ja emitido. Devolve (ok, mensagem)."""
    row = find_certificate(code)
    if not row:
        return False, "Código não encontrado."
    if row["status"] != "valido":
        return False, f"Certificado está '{row['status']}'. Só certificados válidos podem ser enviados."

    cadastrado = (row.get("email") or "").strip()
    destino = (email_informado or "").strip() or cadastrado
    if not destino:
        return False, "Nenhum e-mail cadastrado para este certificado. Informe um no campo ao lado."
    if not email_valido(destino):
        return False, "Endereço de e-mail inválido."

    # Certificado ainda sem endereco: grava antes de tentar enviar, para que o
    # que o operador digitou nao se perca se o envio falhar. Quando ja existe um
    # endereco, ele so e substituido por um envio bem-sucedido (ver
    # update_certificate_email) para nao destruir o que ja funcionou.
    if not cadastrado:
        set_certificate_email(row["codigo_unico"], destino)

    caminho_pdf = Path(row["arquivo_pdf"]) if row.get("arquivo_pdf") else None
    if not caminho_pdf or not caminho_pdf.is_file():
        return False, "PDF do certificado não encontrado em disco. Gere o certificado novamente."
    try:
        pdf_bytes = caminho_pdf.read_bytes()
    except OSError as exc:
        return False, f"Não foi possível ler o PDF do certificado ({exc.strerror or exc})."

    with EnviadorLote(ativo=True) as enviador:
        ok, erro = enviador.enviar(
            destino=destino,
            nome=row["nome"],
            codigo=row["codigo_unico"],
            pdf_bytes=pdf_bytes,
            url_validacao=row.get("url_validacao") or build_validation_url(row["codigo_unico"]),
            evento=row.get("evento") or "",
            nome_arquivo=caminho_pdf.name,
        )

    status = EMAIL_STATUS_ENVIADO if ok else EMAIL_STATUS_FALHOU
    update_certificate_email(row["codigo_unico"], destino, status, None if ok else erro)
    if ok:
        return True, f"Certificado reenviado para {destino}."
    return False, f"Falha no envio: {erro}"


def render_resend_block():
    st.write("Reenviar certificado por e-mail")
    if not smtp_configurado():
        st.info("Reenvio desativado: defina SMTP_HOST e SMTP_USER (ou SMTP_FROM_EMAIL) no servidor.")
        return

    col1, col2, col3 = st.columns([1.2, 1.2, 0.5])
    with col1:
        code = st.text_input("Código para reenviar", key="resend_code")
    with col2:
        email_informado = st.text_input(
            "E-mail (opcional)",
            key="resend_email",
            placeholder="Deixe vazio para usar o cadastrado",
        )
    with col3:
        st.write("")
        st.write("")
        if st.button("Reenviar", type="primary"):
            ok, mensagem = resend_certificate_email(code.strip().upper(), email_informado)
            if ok:
                # Sem o rerun a tabela acima continua mostrando o status antigo,
                # e o operador reenvia de novo achando que nao funcionou.
                st.session_state["resend_feedback"] = mensagem
                st.rerun()
            else:
                st.error(mensagem)

    if st.session_state.get("resend_feedback"):
        st.success(st.session_state.pop("resend_feedback"))


def render_ultimo_lote():
    """Mostra o resultado do ultimo lote, sobrevivendo aos reruns do Streamlit."""
    lote = st.session_state.get("ultimo_lote")
    if not lote:
        return

    resumo = lote["resumo"]
    st.success("Certificados gerados e salvos no banco com sucesso.")
    st.download_button(
        "Baixar ZIP com certificados",
        data=lote["zip"],
        file_name="certificados_com_qrcode.zip",
        mime="application/zip",
    )

    if lote["enviou"]:
        st.write(
            f"**Envio por e-mail:** {resumo['enviados']} enviado(s), "
            f"{resumo['falhas']} falha(s), {resumo['sem_email']} sem e-mail."
        )
        if resumo["erros"]:
            st.warning(
                f"{resumo['falhas']} e-mail(s) não foram entregues. "
                "Os certificados foram gerados normalmente — use o reenvio abaixo na aba "
                "'Certificados Emitidos' depois de corrigir o problema."
            )
            with st.expander("Ver falhas de envio"):
                for erro in resumo["erros"]:
                    st.write(f"- {erro}")

    st.dataframe(pd.DataFrame(lote["rows"]), use_container_width=True)
    if st.button("Limpar resultado"):
        del st.session_state["ultimo_lote"]
        st.rerun()


def render_issued_tab():
    st.subheader("Certificados Emitidos")
    term = st.text_input("Buscar por nome ou código", placeholder="Digite parte do nome ou do código")
    df = search_certificates(term)
    if df.empty:
        st.info("Nenhum certificado encontrado.")
        return

    visible_columns = [
        "codigo_unico",
        "nome",
        "email",
        "email_status",
        "evento",
        "tipo_certificado",
        "status",
        "data_evento",
        "data_emissao",
        "cidade",
        "arquivo_pdf",
    ]
    colunas_presentes = [coluna for coluna in visible_columns if coluna in df.columns]
    st.dataframe(df[colunas_presentes], use_container_width=True, hide_index=True)

    st.divider()
    st.write("Revogar ou cancelar certificado")
    col1, col2, col3 = st.columns([1.2, 0.8, 0.5])
    with col1:
        code = st.text_input("Código", key="status_code")
    with col2:
        status = st.selectbox("Novo status", ["revogado", "cancelado", "valido"])
    with col3:
        st.write("")
        st.write("")
        if st.button("Atualizar", type="primary"):
            if not find_certificate(code):
                st.error("Código não encontrado.")
            else:
                update_certificate_status(code.strip().upper(), status)
                st.success("Status atualizado.")
                st.rerun()

    st.divider()
    render_resend_block()

    st.divider()
    st.write("Excluir certificado")
    del_col1, del_col2, del_col3 = st.columns([1.2, 0.9, 0.5])
    with del_col1:
        delete_code = st.text_input("Código para excluir", key="delete_code")
    with del_col2:
        delete_pdf = st.checkbox("Excluir PDF salvo também")
        confirm_delete = st.checkbox("Confirmo a exclusão")
    with del_col3:
        st.write("")
        st.write("")
        if st.button("Excluir", type="primary"):
            if not confirm_delete:
                st.error("Marque a confirmação antes de excluir.")
            elif delete_certificate(delete_code.strip().upper(), delete_pdf=delete_pdf):
                st.success("Certificado excluído.")
                st.rerun()
            else:
                st.error("Código não encontrado.")


def render_admin_area():
    top_left, top_right = st.columns([1, 0.2])
    with top_left:
        st.title("Gerador de Certificados com QR Code")
        st.caption("Área administrativa do organizador do evento.")
    with top_right:
        if st.button("Sair"):
            st.session_state["admin_authenticated"] = False
            st.rerun()

    tab_generate, tab_issued = st.tabs(["Gerar certificados", "Certificados Emitidos"])
    with tab_generate:
        render_generate_tab()
    with tab_issued:
        render_issued_tab()


init_db()

query_code = st.query_params.get("codigo", "")
query_page = st.query_params.get("pagina", "")

if query_code and query_page != "admin" and not st.session_state.get("admin_authenticated", False):
    render_public_validation(query_code)
elif st.session_state.get("admin_authenticated", False):
    render_admin_area()
else:
    render_login()
