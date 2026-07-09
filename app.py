import hashlib
import io
import re
import secrets
import sqlite3
import string
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import fitz  # PyMuPDF
import pandas as pd
import qrcode
import streamlit as st

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
                hash_pdf_sha256 TEXT
            )
            """
        )
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


def normalize_base_url(url: str) -> str:
    url = (url or "").strip() or "http://localhost:8501/validar"
    return url.rstrip("/")


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


def make_zip(template_bytes: bytes, names, body_text: str, replace_body: bool, prefix: str, event_name: str, cert_type: str, event_date: str, workload: str, location: str, city: str):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    zip_buffer = io.BytesIO()
    merged = fitz.open()
    registry_rows = []
    generated_at = datetime.now().isoformat(timespec="seconds")

    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            code = make_code(prefix)
            validation_url = build_validation_url(code)
            pdf_bytes = make_certificate(template_bytes, name, body_text, replace_body, code, validation_url, show_qr=True)
            pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
            pdf_name = f"certificado_{clean_filename(name)}.pdf"
            pdf_path = OUTPUT_DIR / pdf_name
            pdf_path.write_bytes(pdf_bytes)

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
            }
            insert_certificate(row)
            registry_rows.append(row)

            zf.writestr(pdf_name, pdf_bytes)
            single = fitz.open(stream=pdf_bytes, filetype="pdf")
            merged.insert_pdf(single)
            single.close()

        merged_bytes = io.BytesIO()
        merged.save(merged_bytes, garbage=4, deflate=True)
        merged.close()
        zf.writestr("TODOS_OS_CERTIFICADOS.pdf", merged_bytes.getvalue())
        zf.writestr("certificados_emitidos.csv", dataframe_to_csv_bytes(pd.DataFrame(registry_rows)))
        zf.writestr("LEIA-ME-validacao.txt", make_validation_readme().encode("utf-8"))

    zip_buffer.seek(0)
    return zip_buffer.getvalue(), registry_rows


def make_validation_readme() -> str:
    return f"""VALIDAÇÃO DOS CERTIFICADOS

Cada certificado possui um QR Code único. O QR abre a página pública:
{normalize_base_url(VALIDATION_BASE_URL)}?codigo=CODIGO_DO_CERTIFICADO

O organizador não precisa digitar a URL na tela. Altere VALIDATION_BASE_URL no config.py
ou configure a variável de ambiente VALIDATION_BASE_URL no servidor oficial.
"""


def check_password(password: str) -> bool:
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
    if submitted:
        if secrets.compare_digest(username, ADMIN_USERNAME) and check_password(password):
            st.session_state["admin_authenticated"] = True
            st.rerun()
        else:
            st.error("Usuário ou senha inválidos.")
    st.info("Credenciais padrão para primeiro acesso: usuário admin e senha admin123. Altere no servidor por variáveis de ambiente.")


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


def render_generate_tab():
    left, right = st.columns([1.1, 0.9])
    with left:
        uploaded_template = st.file_uploader("Modelo do certificado em PDF", type=["pdf"])
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

    names = split_names(names_raw)
    if names:
        st.info(f"{len(names)} nome(s) identificado(s).")

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
                zip_bytes, rows = make_zip(
                    template_bytes,
                    names,
                    body_text,
                    replace_body,
                    prefix,
                    event_name,
                    cert_type,
                    event_date,
                    workload,
                    location,
                    city,
                )
                st.success("Certificados gerados e salvos no banco com sucesso.")
                st.download_button(
                    "Baixar ZIP com certificados",
                    data=zip_bytes,
                    file_name="certificados_com_qrcode.zip",
                    mime="application/zip",
                )
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
        except Exception as exc:
            st.exception(exc)


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
        "evento",
        "tipo_certificado",
        "status",
        "data_evento",
        "data_emissao",
        "cidade",
        "arquivo_pdf",
    ]
    st.dataframe(df[visible_columns], use_container_width=True, hide_index=True)

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
