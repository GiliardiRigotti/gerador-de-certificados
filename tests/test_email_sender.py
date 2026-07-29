import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import email_sender


class PlanilhaFake(io.BytesIO):
    """Imita o UploadedFile do Streamlit: bytes + atributo .name."""

    def __init__(self, conteudo: bytes, name: str):
        super().__init__(conteudo)
        self.name = name


def csv_fake(texto: str, name: str = "participantes.csv") -> PlanilhaFake:
    return PlanilhaFake(texto.encode("utf-8"), name)


# --- email_valido ---------------------------------------------------------


@pytest.mark.parametrize(
    "valor",
    ["maria@exemplo.com", "joao.pereira@sub.dominio.gov.br", "  ana@exemplo.org  "],
)
def test_email_valido_aceita_enderecos_corretos(valor):
    assert email_sender.email_valido(valor)


@pytest.mark.parametrize(
    "valor",
    ["joao@", "sem-arroba.com", "@exemplo.com", "a@b", "", None, "dois@ex.com,tres@ex.com"],
)
def test_email_valido_rejeita_enderecos_incorretos(valor):
    assert not email_sender.email_valido(valor)


# --- parse_planilha_destinatarios ----------------------------------------


def test_parse_csv_ponto_e_virgula():
    arquivo = csv_fake("nome;email\nMaria da Silva;maria@exemplo.com\nJoão Pereira;joao@exemplo.com\n")
    assert email_sender.parse_planilha_destinatarios(arquivo) == {
        "Maria da Silva": "maria@exemplo.com",
        "João Pereira": "joao@exemplo.com",
    }


def test_parse_csv_virgula():
    arquivo = csv_fake("nome,email\nAna Souza,ana@exemplo.com\n")
    assert email_sender.parse_planilha_destinatarios(arquivo) == {"Ana Souza": "ana@exemplo.com"}


@pytest.mark.parametrize(
    "cabecalho",
    ["Nome;E-mail", "NOME;EMAIL", "Participante;e_mail", " nome ; Email "],
)
def test_parse_tolera_variacoes_de_cabecalho(cabecalho):
    arquivo = csv_fake(f"{cabecalho}\nAna Souza;ana@exemplo.com\n")
    assert email_sender.parse_planilha_destinatarios(arquivo) == {"Ana Souza": "ana@exemplo.com"}


def test_parse_aceita_utf8_com_bom():
    arquivo = PlanilhaFake(
        "nome;email\nJoão Pereira;joao@exemplo.com\n".encode("utf-8-sig"), "x.csv"
    )
    assert email_sender.parse_planilha_destinatarios(arquivo) == {"João Pereira": "joao@exemplo.com"}


def test_parse_mantem_nome_sem_email_com_string_vazia():
    arquivo = csv_fake("nome;email\nMaria;maria@exemplo.com\nSem Email;\n")
    resultado = email_sender.parse_planilha_destinatarios(arquivo)
    assert resultado == {"Maria": "maria@exemplo.com", "Sem Email": ""}


def test_parse_deduplica_nomes_mantendo_a_primeira_ocorrencia():
    arquivo = csv_fake("nome;email\nMaria;primeira@exemplo.com\nMARIA;segunda@exemplo.com\n")
    assert email_sender.parse_planilha_destinatarios(arquivo) == {"Maria": "primeira@exemplo.com"}


def test_parse_preserva_a_ordem_do_arquivo():
    arquivo = csv_fake("nome;email\nC;c@e.com\nA;a@e.com\nB;b@e.com\n")
    assert list(email_sender.parse_planilha_destinatarios(arquivo)) == ["C", "A", "B"]


def test_parse_ignora_linhas_sem_nome():
    arquivo = csv_fake("nome;email\n;orfao@exemplo.com\nMaria;maria@exemplo.com\n")
    assert email_sender.parse_planilha_destinatarios(arquivo) == {"Maria": "maria@exemplo.com"}


def test_parse_com_separador_sobrando_no_fim_da_linha():
    """Regressao: sem index_col=False o e-mail virava o NOME do participante."""
    arquivo = csv_fake("nome;email\nMaria da Silva;maria@exemplo.com;\n")
    assert email_sender.parse_planilha_destinatarios(arquivo) == {
        "Maria da Silva": "maria@exemplo.com"
    }


def test_parse_com_coluna_extra_sem_cabecalho():
    arquivo = csv_fake("nome;email\nMaria da Silva;maria@exemplo.com;Setor A\n")
    resultado = email_sender.parse_planilha_destinatarios(arquivo)
    assert list(resultado) == ["Maria da Silva"]
    assert resultado["Maria da Silva"] == "maria@exemplo.com"


def test_parse_rejeita_colunas_trocadas():
    """E-mail na coluna de nome significa planilha desalinhada."""
    arquivo = csv_fake("nome;email\nmaria@exemplo.com;Maria da Silva\n")
    with pytest.raises(ValueError, match="trocadas"):
        email_sender.parse_planilha_destinatarios(arquivo)


def test_parse_csv_de_uma_coluna_da_erro_claro():
    """Regressao: o csv.Sniffer elegia a letra 'o' como separador."""
    arquivo = csv_fake("nome\nMaria\nJoao\n")
    with pytest.raises(ValueError) as exc:
        email_sender.parse_planilha_destinatarios(arquivo)
    assert "separar as colunas" in str(exc.value)


def test_parse_aceita_cp1252_do_excel():
    """Traco longo e aspas curvas do Excel nao podem virar caractere de controle."""
    arquivo = PlanilhaFake("nome;email\nJoão – Chefe;joao@exemplo.com\n".encode("cp1252"), "x.csv")
    resultado = email_sender.parse_planilha_destinatarios(arquivo)
    assert resultado == {"João – Chefe": "joao@exemplo.com"}
    assert "\x96" not in list(resultado)[0]


def test_parse_aceita_separador_tab():
    arquivo = csv_fake("nome\temail\nAna Souza\tana@exemplo.com\n")
    assert email_sender.parse_planilha_destinatarios(arquivo) == {"Ana Souza": "ana@exemplo.com"}


def test_parse_falha_sem_coluna_email():
    arquivo = csv_fake("nome;cidade\nMaria;Balneário Camboriú\n")
    with pytest.raises(ValueError, match="email"):
        email_sender.parse_planilha_destinatarios(arquivo)


def test_parse_falha_sem_coluna_nome():
    arquivo = csv_fake("participante_x;email\nMaria;maria@exemplo.com\n")
    with pytest.raises(ValueError, match="nome"):
        email_sender.parse_planilha_destinatarios(arquivo)


def test_parse_falha_quando_nenhum_nome_e_valido():
    arquivo = csv_fake("nome;email\n;a@e.com\n;b@e.com\n")
    with pytest.raises(ValueError, match="Nenhum nome"):
        email_sender.parse_planilha_destinatarios(arquivo)


# --- montar_mensagem ------------------------------------------------------


@pytest.fixture
def smtp_remetente(monkeypatch):
    monkeypatch.setattr(email_sender, "SMTP_FROM_EMAIL", "certificados@dominio.gov.br")
    monkeypatch.setattr(email_sender, "SMTP_FROM_NAME", "Conferência de Saúde")
    monkeypatch.setattr(email_sender, "EMAIL_ASSUNTO", "Seu certificado - {evento}")


def montar(**kwargs):
    padrao = dict(
        destino="maria@exemplo.com",
        nome="Maria da Silva",
        codigo="CMS2026-A8F3K2",
        pdf_bytes=b"%PDF-1.4 conteudo",
        url_validacao="https://certificados.exemplo.gov.br/validar?codigo=CMS2026-A8F3K2",
        evento="9ª Conferência",
    )
    padrao.update(kwargs)
    return email_sender.montar_mensagem(**padrao)


def test_mensagem_tem_um_unico_destinatario(smtp_remetente):
    msg = montar()
    assert msg["To"] == "maria@exemplo.com"
    assert msg["Cc"] is None
    assert msg["Bcc"] is None


def test_mensagem_usa_remetente_configurado(smtp_remetente):
    assert "certificados@dominio.gov.br" in montar()["From"]


def test_assunto_substitui_o_evento(smtp_remetente):
    assert montar()["Subject"] == "Seu certificado - 9ª Conferência"


def test_mensagem_anexa_o_pdf(smtp_remetente):
    anexos = [p for p in montar().iter_attachments()]
    assert len(anexos) == 1
    assert anexos[0].get_content_type() == "application/pdf"
    assert anexos[0].get_filename() == "certificado_Maria_da_Silva.pdf"
    assert anexos[0].get_payload(decode=True) == b"%PDF-1.4 conteudo"


def test_nome_de_arquivo_explicito_prevalece(smtp_remetente):
    msg = montar(nome_arquivo="certificado_custom.pdf")
    assert next(msg.iter_attachments()).get_filename() == "certificado_custom.pdf"


def test_corpo_traz_codigo_e_link_de_validacao(smtp_remetente):
    msg = montar()
    corpo = msg.get_body(preferencelist=("plain",)).get_content()
    assert "CMS2026-A8F3K2" in corpo
    assert "https://certificados.exemplo.gov.br/validar?codigo=CMS2026-A8F3K2" in corpo


def test_corpo_html_escapa_nome_do_participante(smtp_remetente):
    msg = montar(nome="Maria <script>alert(1)</script>")
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# --- EnviadorLote ---------------------------------------------------------


def test_enviador_inativo_nao_envia():
    with email_sender.EnviadorLote(ativo=False) as enviador:
        ok, erro = enviador.enviar("maria@exemplo.com", "Maria", "COD", b"pdf", "http://x")
    assert not ok
    assert erro == email_sender.ERRO_DESATIVADO


def test_enviador_sem_smtp_configurado_nao_envia(monkeypatch):
    monkeypatch.setattr(email_sender, "SMTP_HOST", "")
    with email_sender.EnviadorLote(ativo=True) as enviador:
        ok, erro = enviador.enviar("maria@exemplo.com", "Maria", "COD", b"pdf", "http://x")
    assert not ok
    assert erro == email_sender.ERRO_SEM_CONFIG


def test_enviador_rejeita_email_invalido_antes_de_conectar(monkeypatch, smtp_remetente):
    monkeypatch.setattr(email_sender, "SMTP_HOST", "smtp.exemplo.com")

    def conectar_proibido(self):
        raise AssertionError("nao deveria abrir conexao para endereco invalido")

    monkeypatch.setattr(email_sender.EnviadorLote, "_conectar", conectar_proibido)
    enviador = email_sender.EnviadorLote(ativo=True)
    ok, erro = enviador.enviar("joao@", "João", "COD", b"pdf", "http://x")
    assert not ok
    assert erro == email_sender.ERRO_EMAIL_INVALIDO


def test_falha_de_conexao_nao_levanta_excecao(monkeypatch, smtp_remetente):
    monkeypatch.setattr(email_sender, "SMTP_HOST", "smtp.invalido.exemplo")
    monkeypatch.setattr(
        email_sender.EnviadorLote,
        "_conectar",
        lambda self: setattr(self, "_erro_conexao", "ConnectionRefusedError: recusado"),
    )
    with email_sender.EnviadorLote(ativo=True) as enviador:
        ok, erro = enviador.enviar("maria@exemplo.com", "Maria", "COD", b"pdf", "http://x")
    assert not ok
    assert "recusado" in erro


def test_mensagem_de_erro_nunca_vaza_a_senha(monkeypatch):
    monkeypatch.setattr(email_sender, "SMTP_PASSWORD", "SenhaSuperSecreta123")
    texto = email_sender._mensagem_segura(Exception("auth falhou para SenhaSuperSecreta123"))
    assert "SenhaSuperSecreta123" not in texto
    assert "***" in texto


def test_mensagem_de_erro_e_truncada():
    assert len(email_sender._mensagem_segura(Exception("x" * 500))) <= 200


def test_mensagem_traz_date_e_message_id(smtp_remetente):
    """Sem Date e Message-ID a mensagem viola a RFC 5322 e pontua como spam."""
    msg = montar()
    assert msg["Date"]
    assert msg["Message-ID"].startswith("<") and "dominio.gov.br" in msg["Message-ID"]


def test_destinatario_com_espaco_e_normalizado(smtp_remetente):
    assert montar(destino="  maria@exemplo.com  ")["To"] == "maria@exemplo.com"


class ConexaoFake:
    """SMTP falso que falha de um jeito escolhido pelo teste."""

    def __init__(self, erro=None, vezes=0):
        self.erro = erro
        self.vezes = vezes
        self.enviadas = []
        self.tentativas = 0

    def send_message(self, msg):
        self.tentativas += 1
        if self.erro and self.tentativas <= self.vezes:
            raise self.erro
        self.enviadas.append(msg)

    def quit(self):
        pass


def enviador_com(conexao, monkeypatch):
    monkeypatch.setattr(email_sender, "SMTP_HOST", "smtp.exemplo.com")
    enviador = email_sender.EnviadorLote(ativo=True)
    enviador._conexao = conexao
    monkeypatch.setattr(
        email_sender.EnviadorLote, "_conectar", lambda self: setattr(self, "_conexao", conexao)
    )
    return enviador


def test_erro_permanente_nao_reenvia(monkeypatch, smtp_remetente):
    """Regressao: reenviar apos SMTPDataError pode duplicar o certificado."""
    import smtplib

    conexao = ConexaoFake(erro=smtplib.SMTPDataError(552, b"message too large"), vezes=99)
    enviador = enviador_com(conexao, monkeypatch)
    ok, erro = enviador.enviar("maria@exemplo.com", "Maria", "COD", b"pdf", "http://x")
    assert not ok
    assert conexao.tentativas == 1, "erro permanente nao pode ser retentado"
    assert "552" in erro


def test_queda_de_conexao_reenvia_uma_vez(monkeypatch, smtp_remetente):
    import smtplib

    conexao = ConexaoFake(erro=smtplib.SMTPServerDisconnected("caiu"), vezes=1)
    enviador = enviador_com(conexao, monkeypatch)
    ok, erro = enviador.enviar("maria@exemplo.com", "Maria", "COD", b"pdf", "http://x")
    assert ok, erro
    assert conexao.tentativas == 2
    assert len(conexao.enviadas) == 1, "a mensagem nao pode sair duplicada"


def test_host_morto_para_de_tentar_apos_tres_falhas(monkeypatch, smtp_remetente):
    """Regressao: host inalcancavel custava um timeout inteiro por destinatario."""
    monkeypatch.setattr(email_sender, "SMTP_HOST", "smtp.morto.exemplo")
    tentativas = {"n": 0}

    def conectar_falhando(self):
        tentativas["n"] += 1
        self._conexao = None
        self._erro_conexao = "ConnectionRefusedError: recusado"
        self._falhas_conexao += 1

    monkeypatch.setattr(email_sender.EnviadorLote, "_conectar", conectar_falhando)
    enviador = email_sender.EnviadorLote(ativo=True)
    resultados = [
        enviador.enviar(f"p{i}@exemplo.com", f"P{i}", "COD", b"pdf", "http://x") for i in range(20)
    ]
    assert all(not ok for ok, _ in resultados)
    assert tentativas["n"] <= email_sender.EnviadorLote.MAX_FALHAS_CONEXAO
    assert email_sender.ERRO_SERVIDOR_INACESSIVEL in resultados[-1][1]
