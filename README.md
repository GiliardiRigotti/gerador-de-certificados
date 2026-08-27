# Gerador de Certificados com QR Code

Sistema em Streamlit para o organizador do evento gerar certificados em lote, salvar os registros em SQLite e disponibilizar validação pública por QR Code.

## Como rodar

Windows:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Mac/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Login administrativo

A tela inicial é o login administrativo do organizador.

Defina as credenciais por variáveis de ambiente antes do primeiro acesso:

```bash
ADMIN_USERNAME=seu_usuario
ADMIN_PASSWORD=sua_senha_forte
```

**Não há senha padrão.** Sem `ADMIN_PASSWORD` (ou `ADMIN_PASSWORD_SHA256`) o
login é recusado e a tela mostra:

> Nenhuma senha administrativa configurada. Defina ADMIN_PASSWORD (ou ADMIN_PASSWORD_SHA256) no .env ou no ambiente do servidor.

Também é possível usar o hash SHA-256 da senha em vez do texto puro (tem
precedência sobre `ADMIN_PASSWORD`):

```bash
ADMIN_PASSWORD_SHA256=$(python -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" 'sua_senha_forte')
```

### Onde configurar em cada ambiente

| Ambiente | Como definir |
| --- | --- |
| Local | Colocar `ADMIN_USERNAME` / `ADMIN_PASSWORD` no `.env` (carregado na inicialização). |
| `docker run` | `-e ADMIN_USERNAME=admin -e ADMIN_PASSWORD=...` no comando (recriar o container). |
| docker-compose | Bloco `environment:` do serviço + `docker compose up -d --force-recreate`. |
| Painel de hospedagem | Seção de variáveis de ambiente do app + reiniciar. |

O `.env` **não** vai para produção: está no `.gitignore` e no `.dockerignore`,
portanto nunca entra na imagem nem no repositório. Em produção as variáveis vêm
sempre do ambiente do servidor.

### Diagnóstico de login

O app grava `login_debug.log` (na raiz do projeto; path configurável por
`LOGIN_LOG_PATH`, ignorado pelo git). No start registra se a senha está
configurada e se veio do `os.environ` ou do `.env`; a cada tentativa registra o
match de usuário e o fingerprint SHA-256 da senha recebida vs esperada — sem
gravar a senha em texto puro.

## Configuração

Toda a configuração é feita por variáveis de ambiente. Copie o modelo e ajuste os valores:

```bash
cp .env.example .env
```

O `.env` é carregado automaticamente na inicialização e está no `.gitignore` — nunca o commite.
Variáveis já definidas no ambiente têm precedência sobre o arquivo, então em produção o
servidor (ou o `docker run -e`) continua no comando.

Os padrões, usados quando a variável não é informada, ficam em `config.py`:

```python
VALIDATION_BASE_URL = "https://certificados.bc.sc.gov.br/"
DATABASE_PATH = "certificados.db"
OUTPUT_DIR = "certificados_gerados"
EVENTO_PADRAO = "Nome do evento"
```

O operador não digita mais a URL pública na interface. O QR Code sempre usa `VALIDATION_BASE_URL` e adiciona o código automaticamente:

```text
https://certificados.bc.sc.gov.br/?codigo=CMS2026-A8F3K2
```

## Envio por e-mail

O envio é opcional. Sem `SMTP_HOST` definido, o sistema funciona exatamente como antes e o
envio aparece desabilitado na interface.

```bash
SMTP_HOST=smtp.hostinger.com
SMTP_PORT=465
SMTP_USER=certificados@bc.sc.gov.br
SMTP_PASSWORD=sua_senha
SMTP_FROM_EMAIL=certificados@bc.sc.gov.br   # opcional, padrão: SMTP_USER
SMTP_FROM_NAME=Conferência Municipal de Saúde    # opcional
SMTP_USE_SSL=true                                # false usa STARTTLS; a porta acompanha
SMTP_TIMEOUT=30                                  # opcional, segundos por conexão
EMAIL_ASSUNTO="Seu certificado - {evento}"       # opcional, aceita {evento} e {nome}
```

`SMTP_PORT` é opcional: sem ele a porta acompanha o modo (465 com SSL, 587 com STARTTLS).
Se o servidor ficar inacessível, o envio do lote é interrompido após 3 tentativas de conexão
em vez de esperar o timeout para cada participante.

Cada participante recebe uma mensagem individual com o certificado em PDF anexo e o link de
validação. A lista de destinatários nunca é exposta: um e-mail por pessoa, sem cópia.

### Testar a configuração antes de disparar

Antes de gerar um lote real, confirme que o SMTP funciona:

```bash
python testar_smtp.py                          # só conecta e autentica
python testar_smtp.py --enviar voce@dominio.com  # envia uma mensagem de teste
```

No container:

```bash
docker exec -it certificados python testar_smtp.py
```

Ele testa DNS, porta, TLS e login separadamente, e explica o que fazer em cada falha.
Sem `--enviar`, nenhuma mensagem sai.

**Contas Google (Gmail / Workspace):** a senha da caixa **não funciona** para envio — o
Google desativou autenticação básica em 2022. É preciso uma **Senha de App** de 16
caracteres (exige verificação em duas etapas na conta) ou o **SMTP relay**
(`smtp-relay.gmail.com`) autorizado por IP pelo administrador do domínio. Limite típico:
500 destinatários por dia em conta comum, 2.000 em licenças pagas superiores.

### Planilha de participantes

Para enviar por e-mail, suba uma planilha `.csv` ou `.xlsx` com as colunas `nome` e `email`:

```csv
nome;email
Maria da Silva;maria@exemplo.com
João Pereira;joao@exemplo.com
Ana Souza;
```

O cabeçalho tolera variações (`Nome`, `E-mail`, `NOME`, `Participante`). Vírgula e ponto e
vírgula funcionam como separador. Nomes repetidos geram um único certificado. Linha sem
e-mail gera o certificado normalmente, apenas sem envio.

Falha de e-mail **nunca** impede a emissão: o PDF, o registro no banco e o ZIP são entregues
mesmo com o servidor SMTP fora do ar. Cada certificado guarda seu `email_status`:

| Status | Significado |
|---|---|
| `sem_email` | Nenhum e-mail informado para o participante |
| `nao_enviado` | E-mail cadastrado, mas o envio não foi solicitado na geração |
| `enviado` | Entregue ao servidor SMTP |
| `falhou` | Erro no envio — use o reenvio na aba Certificados Emitidos |

## Como gerar certificados

1. Entre no login administrativo.
2. Abra a aba **Gerar certificados**.
3. Envie o modelo PDF ou use `modelo_certificado.pdf`.
4. Cole os nomes um por linha **ou** envie a planilha com nome e e-mail.
5. Configure dados do evento, carga horária, data, local e cidade.
6. Marque **Enviar certificados por e-mail ao gerar** se quiser o disparo automático.
7. Clique em **Gerar certificados com QR Code**.
8. Baixe o ZIP com os PDFs e confira o resumo do envio.

Cada certificado é salvo no banco SQLite e o PDF também é gravado na pasta definida por `OUTPUT_DIR`.

## Banco SQLite

O banco é criado automaticamente em `certificados.db`. A tabela `certificados` contém:

- `id`
- `codigo_unico`
- `nome`
- `evento`
- `tipo_certificado`
- `carga_horaria`
- `data_evento`
- `data_emissao`
- `cidade`
- `status`
- `arquivo_pdf`
- `criado_em`
- `atualizado_em`
- `email`
- `email_status`
- `email_enviado_em`
- `email_erro`

Os status aceitos são `valido`, `cancelado` e `revogado`.

As colunas de e-mail são adicionadas automaticamente em bancos já existentes, sem perda de
dados — a migração roda no start e é idempotente.

## Certificados Emitidos

Na área administrativa existe a aba **Certificados Emitidos**, com:

- busca por nome ou código;
- listagem dos certificados salvos, incluindo e-mail e status de envio;
- alteração de status para revogar, cancelar ou revalidar certificado;
- reenvio do certificado por e-mail, com opção de corrigir o endereço. Só certificados
  `valido` podem ser reenviados.

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

## Validação pública

Qualquer pessoa que escanear o QR Code acessa a página pública:

```text
/validar?codigo=CODIGO_UNICO
```

A página pública mostra somente:

- certificado válido e seus dados;
- certificado não encontrado;
- certificado inválido, cancelado ou revogado.

Ela não exibe tabela, CSV, banco de dados nem funções administrativas.

## Publicação futura em servidor

Para publicar oficialmente:

1. Hospede o Streamlit em um servidor da instituição ou serviço como Render, Railway, VM ou container.
2. Aponte o domínio `https://certificados.bc.sc.gov.br` para o app. O Streamlit responde na raiz,
   então o QR Code usa `https://certificados.bc.sc.gov.br/?codigo=CODIGO`.
3. Defina `VALIDATION_BASE_URL` no ambiente do servidor antes de gerar certificados oficiais.
4. Defina `ADMIN_USERNAME` e `ADMIN_PASSWORD` (ou `ADMIN_PASSWORD_SHA256`) no ambiente do
   servidor. Sem isso o login é recusado — ver a seção **Login administrativo**.
5. Mantenha o arquivo SQLite com backup regular ou migre futuramente para PostgreSQL/Supabase usando a mesma estrutura de dados.

### Exemplo com Docker

A imagem é construída pelo `Dockerfile` do repositório. As variáveis de ambiente
são passadas na execução (o `.env` não entra na imagem):

```bash
docker build -t certificados .

docker run -d --name certificados \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD=sua_senha_forte \
  -e VALIDATION_BASE_URL=https://certificados.bc.sc.gov.br/ \
  -e SMTP_HOST=smtp.hostinger.com \
  -e SMTP_USER=... -e SMTP_PASSWORD=... \
  -v /srv/certificados/data:/data \
  -p 8501:8501 \
  certificados
```

Para trocar credenciais depois, recrie o container (`docker rm -f certificados`
e rode de novo com os novos `-e`). `docker run -e` não altera container existente.

## Ajuste de layout

As coordenadas do nome e do QR Code estão no início de `app.py`:

```python
NAME_Y = 248
BODY_RECT = fitz.Rect(185, 284, 730, 342)
QR_RECT = fitz.Rect(705, 420, 775, 490)
```

O fundo branco do QR Code é desenhado como retângulo arredondado no PDF, com padding interno e raio aproximado de 15px.
