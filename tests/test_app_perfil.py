"""Testes da geração de certificados por perfil (app.py + engine)."""

import sys
import zipfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "teste.db"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "pdfs"))
    for modulo in ("app", "config", "email_sender", "perfil_certificado"):
        sys.modules.pop(modulo, None)
    import app as modulo_app

    modulo_app.init_db()
    return modulo_app


def ler(app, codigo):
    with app.get_connection() as conn:
        return dict(
            conn.execute(
                "SELECT * FROM certificados WHERE codigo_unico = ?", (codigo,)
            ).fetchone()
        )


def _gerar(app, perfil_id, participantes):
    import perfil_certificado

    perfil = perfil_certificado.carregar_perfil(perfil_id)
    names = list(participantes)
    zip_bytes, rows, resumo = app.make_zip(
        names,
        participantes,
        app.certificado_por_perfil(perfil),
        "ORVS2026",
        "Oficina Regional",
        "Palestrante",
        "27/08/2026",
        "8 horas",
        "Balneário Camboriú",
        "Balneário Camboriú",
        perfil_id=perfil["id"],
        enviar_emails=False,
    )
    return zip_bytes, rows, resumo


# --- geração por perfil ----------------------------------------------------


def test_make_zip_perfil_persiste_campos_novos(app):
    participantes = {
        "João Palestrante": {
            "email": "joao@ex.com",
            "cpf": "123.456.789-00",
            "tema": "Vigilância em portos",
            "horas": "4",
        }
    }
    zip_bytes, rows, _ = _gerar(app, "oficina-palestrante", participantes)

    codigo = rows[0]["codigo_unico"]
    linha = ler(app, codigo)
    assert linha["cpf"] == "123.456.789-00"
    assert linha["tema"] == "Vigilância em portos"
    assert linha["horas"] == "4"
    assert linha["perfil"] == "oficina-palestrante"
    assert Path(linha["arquivo_pdf"]).is_file()

    with zipfile.ZipFile(__import__("io").BytesIO(zip_bytes)) as zf:
        assert any(n.endswith(".pdf") for n in zf.namelist())


def test_make_zip_perfil_coluna_ausente_nao_bloqueia(app):
    participantes = {"Ana Sem Tema": {"email": "ana@ex.com", "cpf": "1", "horas": "2"}}
    _, rows, _ = _gerar(app, "oficina-palestrante", participantes)
    linha = ler(app, rows[0]["codigo_unico"])
    assert linha["tema"] == ""          # campo faltando -> vazio, sem erro
    assert linha["cpf"] == "1"


def test_make_zip_perfil_convidado_so_nome(app):
    participantes = {"Maria Convidada": {"email": ""}}
    import perfil_certificado

    perfil = perfil_certificado.carregar_perfil("oficina-convidado")
    _, rows, _ = app.make_zip(
        ["Maria Convidada"], participantes, app.certificado_por_perfil(perfil),
        "ORVS2026", "Oficina", "Participação", "27/08/2026", "8 horas", "BC", "BC",
        perfil_id="oficina-convidado", enviar_emails=False,
    )
    linha = ler(app, rows[0]["codigo_unico"])
    assert linha["nome"] == "Maria Convidada"
    assert linha["perfil"] == "oficina-convidado"
    assert linha["cpf"] == "" and linha["tema"] == "" and linha["horas"] == ""


# --- ramo legado segue funcionando --------------------------------------


def test_make_zip_legado_continua_funcionando(app):
    template = (RAIZ / "modelo_certificado.pdf").read_bytes()
    participantes = {"Fulano Legado": {"email": ""}}
    _, rows, _ = app.make_zip(
        ["Fulano Legado"],
        participantes,
        app.certificado_legado(template, app.DEFAULT_TEXT_PARTICIPACAO, False),
        "CMS2026", "9a Conferencia", "Participação", "26 e 27 de junho",
        "12 horas", "Centro de Eventos", "Balneário Camboriú",
        enviar_emails=False,
    )
    linha = ler(app, rows[0]["codigo_unico"])
    assert linha["nome"] == "Fulano Legado"
    assert linha["perfil"] == ""       # legado não grava perfil
    assert Path(linha["arquivo_pdf"]).is_file()


# --- consulta pública nunca expõe CPF ----------------------------------


def test_campos_publicos_extra_mostra_tema_horas_nunca_cpf(app):
    row = {"cpf": "999.999.999-99", "tema": "Boas práticas", "horas": "3"}
    extra = app.campos_publicos_extra(row)
    rotulos = [r for r, _ in extra]
    valores = [v for _, v in extra]
    assert rotulos == ["Tema da palestra", "Carga horária da palestra"]
    assert "999.999.999-99" not in valores
    assert not any("cpf" in r.lower() for r in rotulos)


def test_campos_publicos_extra_vazio_quando_sem_dados(app):
    assert app.campos_publicos_extra({"tema": "", "horas": " "}) == []


# --- normalizar_nome_proprio -------------------------------------------


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("VERA SUSANA La Falc", "Vera Susana La Falc"),
        ("William Xavier Oliveira", "William Xavier Oliveira"),  # inalterado
        ("maria da silva", "Maria da Silva"),
        ("JOÃO DE SOUZA DOS SANTOS", "João de Souza dos Santos"),
        ("  ANA   BEATRIZ  ", "Ana Beatriz"),
        ("ana-beatriz DE lima", "Ana-Beatriz de Lima"),
        ("DA SILVA", "Da Silva"),  # partícula no início não vira minúscula
        ("", ""),
        ("McCarthy", "McCarthy"),  # caixa mista preservada
    ],
)
def test_normalizar_nome_proprio(app, entrada, esperado):
    assert app.normalizar_nome_proprio(entrada) == esperado


def test_make_zip_perfil_normaliza_nome_no_pdf_e_no_banco(app):
    import perfil_certificado

    perfil = perfil_certificado.carregar_perfil("oficina-convidado")
    nome_normalizado = app.normalizar_nome_proprio("VERA SUSANA La Falc")
    participantes = {nome_normalizado: {"email": ""}}
    _, rows, _ = app.make_zip(
        [nome_normalizado], participantes, app.certificado_por_perfil(perfil),
        "ORVS2026", "Oficina", "Participação", "27/08/2026", "8 horas", "BC", "BC",
        perfil_id="oficina-convidado", enviar_emails=False,
    )
    assert ler(app, rows[0]["codigo_unico"])["nome"] == "Vera Susana La Falc"
