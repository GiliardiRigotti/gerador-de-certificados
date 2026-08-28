"""Testes do engine de perfis de certificado."""

import json
import sys
from pathlib import Path

import fitz
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import perfil_certificado as pc


def _pdf_em_branco(caminho: Path, largura=869.7, altura=623.8):
    doc = fitz.open()
    doc.new_page(width=largura, height=altura)
    doc.save(caminho)
    doc.close()


@pytest.fixture
def perfil_basico(tmp_path):
    pdf = tmp_path / "modelo.pdf"
    _pdf_em_branco(pdf)
    return {
        "id": "teste",
        "nome": "Perfil de Teste",
        "pdf": str(pdf),
        "colunas_obrigatorias": ["cpf"],
        "campos": [
            {"campo": "nome", "tipo": "linha", "x": 100, "y": 100,
             "tamanho": 14, "largura_max": 300},
            {"campo": "cpf", "tipo": "linha", "x": 100, "y": 150, "tamanho": 11},
            {"campo": "tema", "tipo": "linha", "x": 100, "y": 200,
             "tamanho": 11, "alinhamento": "center"},
        ],
        "qr": {"mostrar": True, "x": 700, "y": 300, "tamanho": 70},
    }


def _texto_do_pdf(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    texto = doc[0].get_text()
    doc.close()
    return texto


# --- renderizar ------------------------------------------------------------


def test_render_completo_desenha_todos_os_campos(perfil_basico):
    dados = {"nome": "Maria Silva", "cpf": "111.222.333-44", "tema": "Boas práticas"}
    out = pc.renderizar(perfil_basico, dados, "COD-1", "https://ex/?codigo=COD-1")
    texto = _texto_do_pdf(out)
    assert "Maria Silva" in texto
    assert "111.222.333-44" in texto
    assert "Boas práticas" in texto
    assert "Código: COD-1" in texto  # rótulo do QR


def test_render_insere_imagem_do_qr(perfil_basico):
    out = pc.renderizar(perfil_basico, {"nome": "X"}, "COD-2", "https://ex/?codigo=COD-2")
    doc = fitz.open(stream=out, filetype="pdf")
    assert len(doc[0].get_images()) >= 1
    doc.close()


def test_coluna_ausente_deixa_campo_em_branco_sem_erro(perfil_basico):
    # Sem 'tema' nos dados: renderiza mesmo assim, sem o texto do tema.
    out = pc.renderizar(perfil_basico, {"nome": "Ana", "cpf": "1"}, "C", "u")
    texto = _texto_do_pdf(out)
    assert "Ana" in texto


def test_valor_vazio_e_none_sao_ignorados(perfil_basico):
    out = pc.renderizar(
        perfil_basico, {"nome": "  ", "cpf": None, "tema": ""}, "C", "u"
    )
    texto = _texto_do_pdf(out)
    assert "Código: C" in texto  # QR saiu; nenhum campo de dado foi desenhado


def test_qr_desligado_nao_desenha(perfil_basico):
    perfil_basico["qr"]["mostrar"] = False
    out = pc.renderizar(perfil_basico, {"nome": "Zé"}, "C", "u")
    doc = fitz.open(stream=out, filetype="pdf")
    assert len(doc[0].get_images()) == 0
    assert "Código:" not in doc[0].get_text()
    doc.close()


def _larguras_span(pdf_bytes, agulha):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    larguras = [
        s["bbox"][2] - s["bbox"][0]
        for b in doc[0].get_text("dict")["blocks"]
        for l in b.get("lines", [])
        for s in l["spans"]
        if agulha in s["text"]
    ]
    doc.close()
    return larguras


def _tamanho_span(pdf_bytes, agulha):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    tamanhos = [
        s["size"]
        for b in doc[0].get_text("dict")["blocks"]
        for l in b.get("lines", [])
        for s in l["spans"]
        if agulha in s["text"]
    ]
    doc.close()
    return tamanhos


def test_autosize_reduz_fonte_para_texto_longo(perfil_basico):
    curto = pc.renderizar(perfil_basico, {"nome": "Ana Lima"}, "C", "u")
    longo = pc.renderizar(
        perfil_basico,
        {"nome": "Maria Aparecida da Silva Goncalves de Oliveira Sa"},
        "C", "u",
    )
    assert _tamanho_span(curto, "Ana Lima")[0] == pytest.approx(14, abs=0.01)
    assert _tamanho_span(longo, "Maria")[0] < 14
    # e o texto longo cabe, folga de ~2% pela diferença de métricas da fonte base
    assert max(_larguras_span(longo, "Maria")) <= 300 * 1.03


def test_texto_patologico_nao_trava_e_ainda_desenha(perfil_basico):
    # Além do mínimo de fonte o texto estoura, mas a geração não pode travar.
    nome = "Wolfeschlegelsteinhausenbergerdorff " * 4
    out = pc.renderizar(perfil_basico, {"nome": nome}, "C", "u")
    assert _larguras_span(out, "Wolfe")


# --- campos_faltando -----------------------------------------------------


def test_campos_faltando_lista_as_chaves_sem_dado(perfil_basico):
    faltando = pc.campos_faltando(perfil_basico, {"nome": "Ana", "cpf": " "})
    assert faltando == ["cpf", "tema"]


# --- validar_perfil ----------------------------------------------------------


@pytest.mark.parametrize(
    "mutacao, trecho",
    [
        (lambda p: p.pop("id"), "sem 'id'"),
        (lambda p: p.pop("nome"), "sem 'nome'"),
        (lambda p: p.pop("campos"), "não tem 'campos'"),
        (lambda p: p["campos"][0].pop("x"), "faltam 'x'"),
        (lambda p: p["campos"][0].update(alinhamento="justo"), "alinhamento"),
        (lambda p: p["campos"].append(dict(p["campos"][0])), "duplicado"),
        (lambda p: p["campos"][0].update(x="aqui"), "número"),
        (lambda p: p.update(qr={"mostrar": True}), "'qr' sem 'x'"),
        (lambda p: p.update(colunas_obrigatorias="cpf"), "deve ser lista"),
    ],
)
def test_validar_perfil_rejeita(perfil_basico, mutacao, trecho):
    mutacao(perfil_basico)
    with pytest.raises(pc.PerfilInvalido) as exc:
        pc.validar_perfil(perfil_basico)
    assert trecho in str(exc.value)


def test_validar_perfil_pdf_inexistente(perfil_basico):
    perfil_basico["pdf"] = "nao/existe.pdf"
    with pytest.raises(pc.PerfilInvalido) as exc:
        pc.validar_perfil(perfil_basico)
    assert "não encontrado" in str(exc.value)


def test_validar_perfil_pdf_corrompido(perfil_basico, tmp_path):
    ruim = tmp_path / "corrompido.pdf"
    ruim.write_bytes(b"%PDF-1.4 lixo lixo nao e um pdf")
    perfil_basico["pdf"] = str(ruim)
    with pytest.raises(pc.PerfilInvalido) as exc:
        pc.validar_perfil(perfil_basico)
    assert "não pôde ser aberto" in str(exc.value)


def test_validar_perfil_aceita_valido(perfil_basico):
    assert pc.validar_perfil(perfil_basico) is perfil_basico


# --- carregar_perfil / listar_perfis (perfis reais do projeto) --------------


def test_carregar_perfil_json_invalido(tmp_path, monkeypatch):
    ruim = tmp_path / "ruim.json"
    ruim.write_text("{ isso não é json", encoding="utf-8")
    monkeypatch.setattr(pc, "PERFIS_DIR", tmp_path)
    with pytest.raises(pc.PerfilInvalido) as exc:
        pc.carregar_perfil("ruim")
    assert "JSON inválido" in str(exc.value)


def test_perfis_do_projeto_sao_validos():
    perfis = pc.listar_perfis()
    ids = {p["id"] for p in perfis}
    assert {"oficina-convidado", "oficina-palestrante"} <= ids


def test_listar_perfis_ignora_invalidos(tmp_path, monkeypatch):
    (tmp_path / "bom.json").write_text(
        json.dumps({
            "id": "bom", "nome": "Bom",
            "pdf": str(RAIZ / "modelos" / "oficina-convidado.pdf"),
            "campos": [{"campo": "nome", "tipo": "linha", "x": 1, "y": 1}],
        }),
        encoding="utf-8",
    )
    (tmp_path / "quebrado.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pc, "PERFIS_DIR", tmp_path)
    ids = {p["id"] for p in pc.listar_perfis()}
    assert ids == {"bom"}


def test_largura_texto_bate_com_o_render_real():
    txt = "Competências Integradas na Fiscalização de Produtos de Origem Animal"
    prevista = pc._largura_texto(txt, "helv", 11)
    doc = fitz.open()
    page = doc.new_page(width=900, height=200)
    page.insert_text((10, 100), txt, fontsize=11, fontname="helv")
    real = _larguras_span(doc.tobytes(), "Competências")[0]
    doc.close()
    assert real == pytest.approx(prevista, abs=1.0)


def test_tema_longo_no_palestrante_nao_invade_com_duracao():
    perfil = pc.carregar_perfil("oficina-palestrante")
    tema = ("Competências Integradas na Fiscalização de Produtos de Origem Animal: "
            "Ações, Intersetorialidade e Parcerias Estratégicas")
    out = pc.renderizar(
        perfil,
        {"nome": "X", "cpf": "1", "tema": tema, "horas": "2 horas"},
        "C", "u",
    )
    # o campo 'tema' começa em x≈147 e não pode passar de ~651 (onde entra "com").
    fim = max(
        s["bbox"][2]
        for b in fitz.open(stream=out, filetype="pdf")[0].get_text("dict")["blocks"]
        for l in b.get("lines", [])
        for s in l["spans"]
        if "Compet" in s["text"]
    )
    assert fim <= 651


def test_render_perfil_palestrante_real():
    perfil = pc.carregar_perfil("oficina-palestrante")
    dados = {"nome": "João Teste", "cpf": "000.000.000-00",
             "tema": "Tema da palestra", "horas": "4"}
    out = pc.renderizar(perfil, dados, "ORVS2026-XYZ", "https://certificados.bc.sc.gov.br/?codigo=ORVS2026-XYZ")
    texto = _texto_do_pdf(out)
    assert "João Teste" in texto
    assert "000.000.000-00" in texto
    assert "Código: ORVS2026-XYZ" in texto
