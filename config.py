import os
from pathlib import Path

from dotenv import load_dotenv


APP_DIR = Path(__file__).parent

# Carrega o .env da raiz do projeto, se existir. Variaveis ja presentes no
# ambiente tem precedencia: em producao o servidor (ou o docker run -e) manda,
# e o arquivo serve so para a maquina do desenvolvedor.
load_dotenv(APP_DIR / ".env")

VALIDATION_BASE_URL = os.getenv(
    "VALIDATION_BASE_URL",
    "https://certificados.seudominio.gov.br/validar",
)
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", APP_DIR / "certificados.db"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", APP_DIR / "certificados_gerados"))

EVENTO_PADRAO = os.getenv(
    "EVENTO_PADRAO",
    "9ª Conferência Municipal de Saúde de Balneário Camboriú",
)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
ADMIN_PASSWORD_SHA256 = os.getenv("ADMIN_PASSWORD_SHA256", "")


VERDADEIRO = {"1", "true", "t", "yes", "y", "sim", "s", "on", "enabled", "ativo"}
FALSO = {"0", "false", "f", "no", "n", "nao", "off", "disabled", "inativo"}


def _inteiro(valor: str, padrao: int) -> int:
    """Converte para int caindo no padrao proprio de cada variavel.

    Cada chamador passa o seu proprio padrao: reaproveitar um fallback de outra
    variavel faz um SMTP_TIMEOUT invalido virar 465 segundos.
    """
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return padrao


def _flag(valor: str, padrao: bool = True) -> bool:
    """Valor desconhecido cai no padrao em vez de virar False silenciosamente."""
    if valor is None or str(valor).strip() == "":
        return padrao
    texto = str(valor).strip().lower()
    if texto in VERDADEIRO:
        return True
    if texto in FALSO:
        return False
    return padrao


# Envio de certificados por e-mail. Sem SMTP_HOST definido, o envio fica desligado
# e a geracao de certificados segue funcionando normalmente.
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_SSL = _flag(os.getenv("SMTP_USE_SSL"), padrao=True)
# A porta acompanha o modo quando nao for informada: 465 para SSL direto,
# 587 para STARTTLS. Fixar 465 com SSL desligado trava a conexao ate o timeout.
SMTP_PORT = _inteiro(os.getenv("SMTP_PORT"), 465 if SMTP_USE_SSL else 587)
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "").strip() or SMTP_USER
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", EVENTO_PADRAO)
SMTP_TIMEOUT = _inteiro(os.getenv("SMTP_TIMEOUT"), 30)

EMAIL_ASSUNTO = os.getenv("EMAIL_ASSUNTO", "Seu certificado - {evento}")
