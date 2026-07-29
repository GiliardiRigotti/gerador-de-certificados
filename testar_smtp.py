#!/usr/bin/env python3
"""Testa a configuracao de SMTP sem gerar certificado nem enviar para participantes.

Uso:
    python testar_smtp.py                        # so conecta e autentica
    python testar_smtp.py --enviar voce@dominio  # tambem envia uma mensagem de teste

Le exatamente as mesmas variaveis de ambiente que o app, e usa o mesmo codigo de
envio (EnviadorLote), para que "passou aqui" signifique "vai passar no app".
"""

import argparse
import socket
import smtplib
import ssl
import sys

from config import (
    SMTP_FROM_EMAIL,
    SMTP_FROM_NAME,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_TIMEOUT,
    SMTP_USE_SSL,
    SMTP_USER,
)
from email_sender import EnviadorLote, email_valido, smtp_configurado


OK = "  [ok]  "
ERRO = "  [erro]"
INFO = "  [--]  "


def dica(mensagem: str):
    print(f"\n  >> {mensagem}\n")


def mostrar_configuracao():
    print("Configuracao lida do ambiente")
    print("-" * 64)
    print(f"  SMTP_HOST       {SMTP_HOST or '(vazio)'}")
    print(f"  SMTP_PORT       {SMTP_PORT}")
    print(f"  SMTP_USE_SSL    {SMTP_USE_SSL}  ({'SSL direto' if SMTP_USE_SSL else 'STARTTLS'})")
    print(f"  SMTP_USER       {SMTP_USER or '(vazio)'}")
    print(f"  SMTP_PASSWORD   {'definida (' + str(len(SMTP_PASSWORD)) + ' caracteres)' if SMTP_PASSWORD else '(vazia)'}")
    print(f"  SMTP_FROM_EMAIL {SMTP_FROM_EMAIL or '(vazio)'}")
    print(f"  SMTP_FROM_NAME  {SMTP_FROM_NAME or '(vazio)'}")
    print(f"  SMTP_TIMEOUT    {SMTP_TIMEOUT}s")
    print("-" * 64)

    if not smtp_configurado():
        print(f"{ERRO} O app considera o SMTP DESLIGADO.")
        dica("Defina ao menos SMTP_HOST e SMTP_USER (ou SMTP_FROM_EMAIL). "
             "Com o SMTP desligado, o app gera certificados normalmente mas nao envia nada.")
        return False

    print(f"{OK} O app considera o SMTP ligado.")

    # Avisar antes de gastar um timeout inteiro descobrindo na conexao.
    if SMTP_PORT == 465 and not SMTP_USE_SSL:
        dica("Porta 465 com SMTP_USE_SSL=false. A 465 espera SSL desde o primeiro byte; "
             "o servidor vai ficar mudo ate o timeout. Use SMTP_USE_SSL=true, ou a porta 587.")
    elif SMTP_PORT == 587 and SMTP_USE_SSL:
        dica("Porta 587 com SMTP_USE_SSL=true. A 587 espera texto puro e depois STARTTLS; "
             "o handshake vai falhar. Use SMTP_USE_SSL=false, ou a porta 465.")
    elif SMTP_PORT == 25:
        dica("Porta 25 e para servidor-a-servidor e costuma ser bloqueada em VPS. "
             "Para envio autenticado use 465 (SSL) ou 587 (STARTTLS).")

    if SMTP_PASSWORD and " " in SMTP_PASSWORD:
        dica("A senha tem espacos. A senha de app do Google e mostrada em 4 blocos, "
             "mas deve ser usada SEM os espacos.")
    if SMTP_HOST.endswith("gmail.com") and SMTP_PASSWORD and len(SMTP_PASSWORD.replace(" ", "")) != 16:
        dica("Host do Google com senha que nao tem 16 caracteres. A senha da caixa NAO funciona "
             "aqui: o Google exige Senha de App (16 letras minusculas) ou SMTP relay.")
    return True


def testar_dns():
    print("\n1. Resolucao de DNS")
    try:
        enderecos = {info[4][0] for info in socket.getaddrinfo(SMTP_HOST, SMTP_PORT)}
        print(f"{OK} {SMTP_HOST} -> {', '.join(sorted(enderecos))}")
        return True
    except socket.gaierror as exc:
        print(f"{ERRO} nao foi possivel resolver {SMTP_HOST}: {exc}")
        dica("Confira se SMTP_HOST esta escrito certo (ex.: smtp.gmail.com).")
        return False


def testar_porta():
    print("\n2. Conexao TCP")
    try:
        with socket.create_connection((SMTP_HOST, SMTP_PORT), timeout=SMTP_TIMEOUT):
            print(f"{OK} porta {SMTP_PORT} aberta e aceitando conexao")
        return True
    except socket.timeout:
        print(f"{ERRO} timeout ao conectar na porta {SMTP_PORT}")
        dica("Timeout normalmente e firewall de saida. Muitos provedores de VPS bloqueiam "
             "portas de e-mail por padrao — peca a liberacao da porta de saida ao suporte.")
        return False
    except OSError as exc:
        print(f"{ERRO} nao foi possivel conectar: {exc}")
        dica("Conexao recusada costuma ser porta errada. Use 465 com SMTP_USE_SSL=true "
             "ou 587 com SMTP_USE_SSL=false.")
        return False


def testar_handshake_e_login():
    print("\n3. TLS e autenticacao")
    contexto = ssl.create_default_context()
    conexao = None
    try:
        if SMTP_USE_SSL:
            conexao = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT, context=contexto)
            print(f"{OK} handshake SSL concluido")
        else:
            conexao = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT)
            conexao.ehlo()
            if not conexao.has_extn("starttls"):
                print(f"{ERRO} o servidor nao oferece STARTTLS nesta porta")
                dica("Se a porta for 465, use SMTP_USE_SSL=true.")
                return False
            conexao.starttls(context=contexto)
            conexao.ehlo()
            print(f"{OK} STARTTLS concluido")

        if not SMTP_USER:
            print(f"{INFO} SMTP_USER vazio: pulando login (relay autorizado por IP)")
            return True

        conexao.login(SMTP_USER, SMTP_PASSWORD)
        print(f"{OK} autenticado como {SMTP_USER}")
        return True

    except smtplib.SMTPAuthenticationError as exc:
        codigo = getattr(exc, "smtp_code", "?")
        print(f"{ERRO} autenticacao recusada (codigo {codigo})")
        if SMTP_HOST.endswith("gmail.com"):
            dica("O Google recusa a senha normal da caixa desde 2022. Voce precisa de:\n"
                 "     - Senha de App: ative a verificacao em duas etapas na conta e gere em\n"
                 "       myaccount.google.com > Seguranca > Senhas de app (16 caracteres); ou\n"
                 "     - SMTP relay: o administrador do dominio libera smtp-relay.gmail.com\n"
                 "       autorizando o IP deste servidor.")
        else:
            dica("Confira usuario e senha. Alguns servidores exigem o endereco completo como usuario.")
        return False
    except ssl.SSLError as exc:
        print(f"{ERRO} falha de TLS: {exc}")
        dica("Combinacao de porta e modo incompativel. Use 465 com SSL ou 587 com STARTTLS.")
        return False
    except (OSError, smtplib.SMTPException) as exc:
        print(f"{ERRO} {type(exc).__name__}: {exc}")
        return False
    finally:
        if conexao is not None:
            try:
                conexao.quit()
            except Exception:
                pass


def enviar_teste(destino: str):
    print("\n4. Envio de mensagem de teste")
    if not email_valido(destino):
        print(f"{ERRO} endereco invalido: {destino}")
        return False

    pdf_falso = b"%PDF-1.4\n% certificado de teste\n"
    with EnviadorLote(ativo=True) as enviador:
        ok, erro = enviador.enviar(
            destino=destino,
            nome="Teste de Configuracao",
            codigo="TESTE-000000",
            pdf_bytes=pdf_falso,
            url_validacao="https://exemplo.invalido/validar?codigo=TESTE-000000",
            evento="Teste de configuracao do sistema de certificados",
            nome_arquivo="certificado_teste.pdf",
        )
    if ok:
        print(f"{OK} mensagem aceita pelo servidor e enviada para {destino}")
        dica("Confira a caixa de entrada e o spam. O anexo e um PDF minimo de teste, "
             "nao um certificado real.")
        return True

    print(f"{ERRO} o envio falhou: {erro}")
    return False


def main():
    parser = argparse.ArgumentParser(description="Testa a configuracao de SMTP do gerador de certificados.")
    parser.add_argument("--enviar", metavar="EMAIL",
                        help="envia uma mensagem de teste para este endereco")
    args = parser.parse_args()

    print()
    if not mostrar_configuracao():
        return 1

    for etapa in (testar_dns, testar_porta, testar_handshake_e_login):
        if not etapa():
            print("\nResultado: FALHOU. Corrija o item acima e rode de novo.\n")
            return 1

    if args.enviar:
        if not enviar_teste(args.enviar):
            print("\nResultado: conexao ok, mas o envio falhou.\n")
            return 1
    else:
        print(f"\n{INFO} Nenhuma mensagem foi enviada.")
        print(f"{INFO} Para testar o envio de verdade: python testar_smtp.py --enviar voce@dominio.com")

    print("\nResultado: OK. O app consegue enviar com esta configuracao.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
