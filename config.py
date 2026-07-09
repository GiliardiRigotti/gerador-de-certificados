import os
from pathlib import Path


APP_DIR = Path(__file__).parent

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
