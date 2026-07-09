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

Credenciais padrão para primeiro acesso:

```text
Usuário: admin
Senha: admin123
```

Em produção, altere por variáveis de ambiente:

```bash
ADMIN_USERNAME=seu_usuario
ADMIN_PASSWORD=sua_senha_forte
```

Também é possível usar hash SHA-256 da senha:

```bash
ADMIN_PASSWORD_SHA256=hash_da_senha
```

## Configuração

As configurações principais ficam em `config.py`:

```python
VALIDATION_BASE_URL = "https://certificados.seudominio.gov.br/validar"
DATABASE_PATH = "certificados.db"
OUTPUT_DIR = "certificados_gerados"
EVENTO_PADRAO = "Nome do evento"
```

O operador não digita mais a URL pública na interface. O QR Code sempre usa `VALIDATION_BASE_URL` e adiciona o código automaticamente:

```text
https://certificados.seudominio.gov.br/validar?codigo=CMS2026-A8F3K2
```

## Como gerar certificados

1. Entre no login administrativo.
2. Abra a aba **Gerar certificados**.
3. Envie o modelo PDF ou use `modelo_certificado.pdf`.
4. Cole os nomes, um por linha.
5. Configure dados do evento, carga horária, data, local e cidade.
6. Clique em **Gerar certificados com QR Code**.
7. Baixe o ZIP com os PDFs.

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

Os status aceitos são `valido`, `cancelado` e `revogado`.

## Certificados Emitidos

Na área administrativa existe a aba **Certificados Emitidos**, com:

- busca por nome ou código;
- listagem dos certificados salvos;
- alteração de status para revogar, cancelar ou revalidar certificado.

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
2. Configure um domínio público, por exemplo `https://certificados.seudominio.gov.br/validar`.
3. Defina `VALIDATION_BASE_URL` no ambiente do servidor antes de gerar certificados oficiais.
4. Defina credenciais administrativas fortes.
5. Mantenha o arquivo SQLite com backup regular ou migre futuramente para PostgreSQL/Supabase usando a mesma estrutura de dados.

## Ajuste de layout

As coordenadas do nome e do QR Code estão no início de `app.py`:

```python
NAME_Y = 248
BODY_RECT = fitz.Rect(185, 284, 730, 342)
QR_RECT = fitz.Rect(705, 420, 775, 490)
```

O fundo branco do QR Code é desenhado como retângulo arredondado no PDF, com padding interno e raio aproximado de 15px.
