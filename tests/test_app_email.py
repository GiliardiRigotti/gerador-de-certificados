"""Testes das partes de e-mail que vivem em app.py (banco e nomes de arquivo)."""

import sqlite3
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Importa app.py apontando para um banco descartavel."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "teste.db"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "pdfs"))
    for modulo in ("app", "config", "email_sender"):
        sys.modules.pop(modulo, None)
    import app as modulo_app

    modulo_app.init_db()
    return modulo_app


def certificado_base(app, codigo, **extra):
    linha = {
        "codigo_unico": codigo,
        "nome": "Maria da Silva",
        "evento": "9a Conferencia",
        "tipo_certificado": "Participacao",
        "carga_horaria": "12 horas",
        "data_evento": "26 e 27 de junho",
        "data_emissao": "2026-06-01T10:00:00",
        "cidade": "Balneario Camboriu",
        "status": "valido",
        "arquivo_pdf": "",
        "criado_em": "2026-06-01T10:00:00",
        "atualizado_em": "2026-06-01T10:00:00",
        "local": "Centro de Eventos",
        "url_validacao": f"https://exemplo.gov.br/validar?codigo={codigo}",
        "hash_pdf_sha256": "abc",
        "email": "",
        "email_status": "sem_email",
    }
    linha.update(extra)
    app.insert_certificate(linha)
    return linha


def ler(app, codigo):
    with app.get_connection() as conn:
        return dict(conn.execute(
            "SELECT * FROM certificados WHERE codigo_unico = ?", (codigo,)
        ).fetchone())


# --- unique_pdf_name ------------------------------------------------------


def test_nomes_que_colapsam_recebem_arquivos_distintos(app):
    """Regressao: 'Ana  Souza' e 'Ana Souza' sobrescreviam o mesmo PDF."""
    usados = set()
    a = app.unique_pdf_name("Ana  Souza", "COD-A", usados)
    b = app.unique_pdf_name("Ana Souza", "COD-B", usados)
    assert a != b
    assert "COD-B" in b


def test_nomes_sem_caracteres_latinos_nao_colidem(app):
    """Regressao: nomes em chines/grego viravam todos 'certificado_certificado.pdf'."""
    usados = set()
    nomes = [app.unique_pdf_name(n, f"COD-{i}", usados) for i, n in enumerate(["张伟", "Ωμέγα", "🎓"])]
    assert len(set(nomes)) == 3
    assert all("certificado_certificado.pdf" != n for n in nomes)


def test_nome_normal_permanece_legivel(app):
    assert app.unique_pdf_name("Maria da Silva", "COD-1", set()) == "certificado_Maria_da_Silva.pdf"


# --- update_certificate_email --------------------------------------------


def test_envio_bem_sucedido_grava_endereco_e_data(app):
    certificado_base(app, "COD-1")
    app.update_certificate_email("COD-1", "maria@exemplo.com", app.EMAIL_STATUS_ENVIADO)
    linha = ler(app, "COD-1")
    assert linha["email"] == "maria@exemplo.com"
    assert linha["email_status"] == "enviado"
    assert linha["email_enviado_em"]
    assert linha["email_erro"] is None


def test_falha_no_reenvio_preserva_entrega_anterior(app):
    """Regressao critica: um reenvio falho apagava a prova de entrega."""
    certificado_base(app, "COD-2")
    app.update_certificate_email("COD-2", "maria@exemplo.com", app.EMAIL_STATUS_ENVIADO)
    entrega = ler(app, "COD-2")["email_enviado_em"]

    app.update_certificate_email("COD-2", "maria@digitado.errado", app.EMAIL_STATUS_FALHOU, "recusado")

    linha = ler(app, "COD-2")
    assert linha["email"] == "maria@exemplo.com", "o endereco que funcionou nao pode ser sobrescrito"
    assert linha["email_enviado_em"] == entrega, "a data de entrega nao pode ser apagada"
    assert linha["email_status"] == "falhou"
    assert linha["email_erro"] == "recusado"


def test_falha_sem_endereco_previo_registra_o_tentado(app):
    certificado_base(app, "COD-3")
    app.update_certificate_email("COD-3", "tentado@exemplo.com", app.EMAIL_STATUS_FALHOU, "sem rota")
    linha = ler(app, "COD-3")
    assert linha["email"] == "tentado@exemplo.com"
    assert linha["email_enviado_em"] is None


def test_qualquer_gravacao_atualiza_o_carimbo_da_linha(app):
    certificado_base(app, "COD-4")
    app.update_certificate_email("COD-4", "x@exemplo.com", app.EMAIL_STATUS_FALHOU, "erro")
    assert ler(app, "COD-4")["atualizado_em"] != "2026-06-01T10:00:00"


# --- migracao -------------------------------------------------------------


def test_migracao_e_idempotente_e_preserva_dados(tmp_path, monkeypatch):
    """Banco no formato antigo ganha as colunas sem perder registros."""
    caminho = tmp_path / "antigo.db"
    conn = sqlite3.connect(caminho)
    conn.execute(
        """CREATE TABLE certificados (
            id INTEGER PRIMARY KEY AUTOINCREMENT, codigo_unico TEXT NOT NULL UNIQUE,
            nome TEXT NOT NULL, evento TEXT NOT NULL, tipo_certificado TEXT NOT NULL,
            carga_horaria TEXT NOT NULL, data_evento TEXT NOT NULL, data_emissao TEXT NOT NULL,
            cidade TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'valido', arquivo_pdf TEXT,
            criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL, local TEXT,
            url_validacao TEXT, hash_pdf_sha256 TEXT)"""
    )
    conn.execute(
        "INSERT INTO certificados (codigo_unico,nome,evento,tipo_certificado,carga_horaria,"
        "data_evento,data_emissao,cidade,status,criado_em,atualizado_em) VALUES "
        "('ANTIGO-1','Joao Antigo','Ev','P','12h','26/06','2026-06-01','BC','valido','x','y')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("DATABASE_PATH", str(caminho))
    for modulo in ("app", "config", "email_sender"):
        sys.modules.pop(modulo, None)
    import app as modulo_app

    modulo_app.init_db()
    modulo_app.init_db()  # segunda vez nao pode dar "duplicate column"

    linha = ler(modulo_app, "ANTIGO-1")
    assert linha["nome"] == "Joao Antigo"
    assert linha["email_status"] == "sem_email"
    assert linha["email"] is None
