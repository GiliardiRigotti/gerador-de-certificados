---
title: 'Disparo de e-mail ao gerar certificado'
type: 'feature'
created: '2026-07-27'
status: 'done'
baseline_commit: 'd77bd46'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Hoje o operador gera o lote, baixa o ZIP e precisa distribuir os certificados manualmente, um a um. O sistema não guarda o e-mail do participante nem tem qualquer canal de envio.

**Approach:** Passar a aceitar uma planilha `nome;email` na aba de geração, persistir o e-mail e o status de envio no SQLite, e disparar automaticamente (via SMTP configurado por variáveis de ambiente) um e-mail com o PDF anexo e o link de validação, com reenvio manual na aba "Certificados Emitidos".

## Boundaries & Constraints

**Always:**
- Falha de e-mail **nunca** aborta a geração: o certificado, o PDF em disco, o registro no banco e o ZIP são entregues mesmo se todo o SMTP estiver fora do ar.
- Uma única conexão SMTP reaproveitada para o lote inteiro; cada destinatário tem seu próprio try/except e grava o resultado no banco.
- Migração do banco é aditiva e idempotente (`PRAGMA table_info` + `ALTER TABLE`): o `certificados.db` existente já tem 5 registros e não pode ser recriado.
- Credenciais SMTP só via ambiente. Nenhum segredo em código, em log ou na tela.
- O textarea de nomes continua funcionando exatamente como hoje para quem não vai enviar e-mail.

**Ask First:**
- Qualquer mudança no layout do PDF, nas coordenadas ou no `modelo_certificado.pdf`.
- Trocar SQLite por outro banco, ou introduzir fila/worker assíncrono (Celery, RQ, threads).
- Reduzir o peso do PDF (3,2 MB/certificado) recomprimindo o template.

**Never:**
- Não enviar e-mail de certificado com `status != 'valido'` (cancelado/revogado).
- Não expor a lista de destinatários no cabeçalho: um e-mail por participante, nunca CC/BCC em massa.
- Não alterar a página pública de validação nem o fluxo de login.
- Não adicionar provider SDK (SendGrid, SES, Mailgun) — apenas `smtplib` da stdlib.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Envio feliz | Planilha com nome+email válido, SMTP ok | Certificado gerado; e-mail com PDF anexo + link enviado; `email_status='enviado'`, `email_enviado_em` preenchido | N/A |
| Sem e-mail na planilha | Linha só com nome (coluna email vazia) | Certificado gerado e incluído no ZIP normalmente; `email_status='sem_email'` | Não é erro — não conta como falha na UI |
| E-mail malformado | `joao@`, `sem-arroba.com` | Não tenta enviar; `email_status='falhou'`, `email_erro='endereço inválido'` | Validação antes de abrir a conexão |
| SMTP indisponível / auth falha | Host errado ou senha inválida | ZIP e banco entregues; lote todo com `email_status='falhou'`; `st.warning` com contagem e motivo genérico | Exceção capturada, **sem** vazar senha na mensagem |
| Queda no meio do lote | Conexão cai no destinatário 30 de 100 | 1–29 permanecem `enviado`; 30+ ficam `falhou`; geração conclui | Reconecta uma vez; se falhar, marca o restante |
| SMTP não configurado | `SMTP_HOST` vazio | Checkbox de envio desabilitado com aviso; geração idêntica ao comportamento atual | N/A |
| Reenvio de revogado | Operador pede reenvio de certificado `revogado` | Envio bloqueado com mensagem explicativa | Nenhum e-mail sai |
| Reenvio sem e-mail cadastrado | Certificado antigo, coluna `email` nula | Campo para informar o e-mail, persistido antes do envio | Erro claro se continuar vazio |
| Nomes duplicados na planilha | Mesmo nome duas vezes | Dedupe preservado (comportamento atual de `split_names`); um certificado, um e-mail | N/A |

</frozen-after-approval>

## Code Map

- `app.py:70` `init_db()` -- criar tabela; precisa ganhar migração aditiva das colunas novas
- `app.py:106` `insert_certificate()` -- lista fixa de campos; incluir `email` e `email_status`
- `app.py:312` `make_zip()` -- ponto único de geração (código → PDF → hash → insert → ZIP); é onde o disparo se encaixa, após `insert_certificate`
- `app.py:206` `split_names()` -- parser do textarea; **não alterar**, a planilha é caminho paralelo
- `app.py:497` `render_generate_tab()` -- UI de geração; recebe o uploader da planilha e o checkbox de envio
- `app.py:575` `render_issued_tab()` -- UI de emitidos; recebe a coluna de status e o bloco de reenvio
- `app.py:239` `build_validation_url()` -- monta o link de validação usado no corpo do e-mail
- `config.py` -- padrão `os.getenv` já estabelecido; recebe as chaves SMTP
- `certificados.db` -- 5 registros existentes, migração obrigatória
- `modelo_certificado.pdf` -- 3,2 MB; define o peso de cada anexo

## Tasks & Acceptance

**Execution:**
- [x] `config.py` -- adicionar `SMTP_HOST`, `SMTP_PORT` (default 465), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME`, `SMTP_USE_SSL` (default True) e `EMAIL_ASSUNTO` via `os.getenv` -- mantém o padrão existente e tira segredo do código
- [x] `email_sender.py` -- novo módulo: `smtp_configurado()`, `email_valido()`, `parse_planilha_destinatarios()` (CSV/XLSX → `{nome: email}`, tolerante a `nome`/`Nome`/`name` e `email`/`e-mail`), `montar_mensagem()` (EmailMessage, PDF anexo, corpo texto+HTML com link de validação) e `EnviadorLote` (context manager que abre/fecha uma conexão e expõe `enviar(...) -> (ok, erro)`) -- isola SMTP de UI e mantém `app.py` (663 linhas) sob controle
- [x] `app.py` `init_db()` -- migração aditiva idempotente: `PRAGMA table_info` + `ALTER TABLE` para `email TEXT`, `email_status TEXT DEFAULT 'sem_email'`, `email_enviado_em TEXT`, `email_erro TEXT` -- preserva os 5 registros existentes
- [x] `app.py` `insert_certificate()` + `make_zip()` -- propagar `email` no row; após cada `insert_certificate`, enviar pelo `EnviadorLote` e atualizar o status por código; retornar contagens `(enviados, falhas, sem_email)` -- mantém o ponto único de geração
- [x] `app.py` `render_generate_tab()` -- uploader de planilha (csv/xlsx), pré-visualização do casamento nome↔e-mail, checkbox "Enviar por e-mail" (desabilitado sem SMTP), `st.progress` durante o lote e resumo do envio ao final -- dá visibilidade em envio síncrono
- [x] `app.py` `render_issued_tab()` -- exibir `email` e `email_status` nas colunas visíveis e adicionar bloco "Reenviar certificado por e-mail" (código + e-mail opcional), bloqueando status diferente de `valido` -- recuperação das falhas
- [x] `tests/test_email_sender.py` -- testes unitários da matriz de I/O nas funções puras: parsing da planilha (variações de cabeçalho, e-mail vazio, duplicados), `email_valido()` e `montar_mensagem()` (anexo presente, link correto, um destinatário por mensagem)
- [x] `requirements.txt` / `requirements-dev.txt` -- adicionar `openpyxl==3.1.5` (leitura de xlsx pelo pandas) em prod e `pytest==8.3.3` em dev -- pytest fora da imagem Docker
- [x] `README.md` + `Dockerfile` -- documentar as variáveis SMTP e o formato da planilha; copiar `email_sender.py` na imagem -- sem isso o container quebra no import

**Acceptance Criteria:**
- Given o `certificados.db` atual com 5 registros, when o app sobe após a mudança, then as colunas novas existem, os 5 registros seguem legíveis com `email_status='sem_email'` e um segundo start não gera erro de coluna duplicada.
- Given SMTP configurado e planilha de 3 participantes válidos, when o operador gera com o envio marcado, then o ZIP é baixável, os 3 chegam com o PDF anexo e a UI mostra "3 enviados, 0 falhas".
- Given o envio marcado e o SMTP com senha errada, when o operador gera, then o ZIP e os registros são criados normalmente, a UI avisa a falha e nenhuma senha aparece na tela, no log ou no banco.
- Given um certificado com `email_status='falhou'`, when o operador usa o reenvio na aba Emitidos e o SMTP volta, then o e-mail sai e o status vira `enviado` sem gerar novo certificado nem novo código.
- Given nenhuma variável SMTP definida, when o operador gera certificados, then o fluxo é idêntico ao de hoje e nada relacionado a e-mail falha.

## Spec Change Log

### 2026-07-29 — Revisão adversarial (3 revisores), iteração 1

Todas as correções abaixo são de implementação; nenhuma renegocia o bloco congelado.

**Corrupção de dados**
- *Achado:* um reenvio que falhava sobrescrevia o e-mail bom e zerava `email_enviado_em`, destruindo a prova de uma entrega anterior.
- *Correção:* `update_certificate_email` separa sucesso de falha. Na falha, `email_enviado_em` fica fora do `SET` e o endereço só é gravado se ainda não houvesse nenhum (`COALESCE(NULLIF(email,''), ?)`).
- *Estado ruim evitado:* registro de certificado entregue virando "nunca entregue, endereço errado", sem recuperação.

**Colunas desalinhadas na planilha**
- *Achado:* `nome;email` com `;` sobrando no fim da linha produzia `{'maria@ex.com': ''}` — o e-mail virava o nome impresso no certificado.
- *Correção:* `index_col=False` no `read_csv`, separadores explícitos (`;`, `,`, tab) no lugar de `sep=None`, e recusa explícita quando um nome casa com o regex de e-mail.
- *Estado ruim evitado:* certificado oficial emitido com endereço de e-mail no lugar do nome da pessoa.

**Invariante "falha de e-mail nunca aborta a geração"**
- *Achado:* `update_certificate_email` era chamado sem `try/except` dentro do loop; um `database is locked` propagava e matava a geração no meio.
- *Correção:* `try/except sqlite3.Error` em volta, registrando no resumo.

**Colisão de nome de arquivo**
- *Achado:* `clean_filename` colapsa nomes distintos e devolve `certificado` para nomes sem caracteres latinos — PDFs se sobrescreviam e o reenvio entregava o certificado da pessoa errada.
- *Correção:* `unique_pdf_name()` desambigua pelo código único dentro do lote.

**Duplicidade de emissão e de envio**
- *Achado:* o resultado vivia dentro do `if st.button(...)`; qualquer rerun do Streamlit fazia o ZIP sumir, levando o operador a clicar de novo e emitir/enviar tudo em duplicata.
- *Correção:* resultado persistido em `st.session_state["ultimo_lote"]`, renderizado fora do branch, com aviso explícito e botão de limpar.

**Falha silenciosa da planilha**
- *Achado:* planilha ilegível caía calada na lista do textarea; o operador via "gerado com sucesso" e acreditava que os e-mails saíram.
- *Correção:* erro de parsing bloqueia a geração.

**SMTP**
- Retry cego trocado por taxonomia permanente vs. transitório (`ERROS_PERMANENTES` / `ERROS_DE_CONEXAO`) — reenviar após `SMTPDataError` podia duplicar mensagem já aceita.
- Circuit breaker `MAX_FALHAS_CONEXAO = 3`: host inalcançável custava um timeout inteiro por destinatário (~50 min num lote de 100).
- `Date` e `Message-ID` adicionados — sem eles a mensagem viola a RFC 5322 e pontua como spam.
- Conexão passou a ser preguiçosa: lote só de endereços inválidos não toca a rede.
- E-mail informado no reenvio é persistido **antes** do envio quando não havia endereço cadastrado (linha "Reenvio sem e-mail cadastrado" da matriz).

**Config** (desvios do texto da task, deliberados)
- `SMTP_TIMEOUT` ganhou parser próprio (`_inteiro`): reusar o fallback de porta fazia `SMTP_TIMEOUT=30s` virar **465 segundos**.
- `SMTP_PORT` passou de "default 465" para `465 if SMTP_USE_SSL else 587` — 465 com SSL desligado trava até o timeout.
- `_flag` cai no padrão em valores desconhecidos; antes `SMTP_USE_SSL=y` virava `False` silenciosamente.

**KEEP — o que funcionou e deve sobreviver a qualquer re-derivação**
- A separação `email_sender.py` / `app.py`: o contrato "`enviar()` nunca levanta exceção" foi validado pelos três revisores.
- `make_zip` como ponto único de geração, com o envio **depois** do `insert_certificate`.
- A migração aditiva por `PRAGMA table_info` + `ALTER TABLE`.
- `_mensagem_segura()` redigindo credenciais — verificado com senha real, não vazou para tela, log nem banco.
- `split_names()` intocado; a planilha como caminho paralelo.

**Ainda em aberto (decisão do humano)**
- Bump do PyMuPDF `1.24.7 → 1.28.0`: veio de fora desta story (já estava na árvore), sem justificativa registrada e sem teste cobrindo layout do PDF.
- `email` dos participantes entra no `certificados_emitidos.csv` dentro do ZIP distribuível.

## Design Notes

**Peso do anexo:** cada PDF tem 3,2 MB (o template inteiro é copiado por certificado) e o base64 do e-mail infla ~33% → ~4,3 MB por mensagem. Fica sob o limite típico de 25 MB, mas um lote de 100 leva minutos em envio síncrono. Daí o `st.progress` e a conexão única reaproveitada. Recomprimir o template resolveria de vez, mas está em **Ask First**.

**Envio síncrono é decisão consciente:** Streamlit reexecuta o script a cada interação; introduzir threads criaria estado órfão entre reruns. Para o volume esperado (uma conferência municipal), o envio inline com progresso é a escolha certa.

**Contrato do enviador** — a UI nunca fala com `smtplib` direto:

```python
with EnviadorLote() as enviador:          # abre 1 conexão; no-op se não configurado
    ok, erro = enviador.enviar(
        destino=email, nome=nome, codigo=code,
        pdf_bytes=pdf_bytes, url_validacao=validation_url,
    )                                      # nunca levanta; erro é str ou None
```

**Status de e-mail** (`email_status`): `sem_email` | `nao_enviado` | `enviado` | `falhou`. Sem estado `pendente` — não há fila.

`nao_enviado` foi acrescentado na implementação: quando o operador sobe a planilha com e-mails mas desmarca o envio, o endereço fica gravado e marcar como `sem_email` seria mentira. Não conflita com nenhuma linha da matriz de I/O — `sem_email` segue significando "participante sem endereço".

## Verification

**Commands:**
- `python -m pytest tests/ -v` -- expected: todos os testes da matriz de I/O passam
- `python -c "import app"` -- expected: importa sem erro e a migração roda de forma idempotente
- `sqlite3 certificados.db "PRAGMA table_info(certificados);"` -- expected: as 4 colunas novas presentes, os 5 registros originais intactos
- `streamlit run app.py` -- expected: sobe sem SMTP configurado, com o checkbox de envio desabilitado e a geração funcionando como hoje

**Manual checks (if no CLI):**
- Com SMTP real, gerar 1 certificado de teste: confirmar recebimento, o PDF anexo abrindo corretamente e o link de validação levando à página pública com o código certo.

## Suggested Review Order

**Ponto de entrada — o disparo**

- Ponto único de geração; envio acontece só depois do certificado estar salvo.
  [`app.py:407`](../app.py#L407)

- Contrato central: `enviar()` nunca levanta exceção, devolve `(ok, erro)`.
  [`email_sender.py:196`](../email_sender.py#L196)

**Integridade dos dados (as correções mais críticas)**

- Falha de reenvio não pode apagar o endereço bom nem a prova de entrega.
  [`app.py:220`](../app.py#L220)

- `index_col=False` impede o e-mail de ocupar a coluna do nome.
  [`email_sender.py:88`](../email_sender.py#L88)

- Nomes que colapsam no mesmo arquivo são desambiguados pelo código.
  [`app.py:283`](../app.py#L283)

- Migração aditiva idempotente; preserva os registros existentes.
  [`app.py:91`](../app.py#L91)

**Resiliência do SMTP**

- Erro permanente não é retentado — reenviar duplicaria a mensagem.
  [`email_sender.py:36`](../email_sender.py#L36)

- Circuit breaker: host morto para de ser tentado após 3 falhas.
  [`email_sender.py:243`](../email_sender.py#L243)

- Credencial redigida antes de chegar à tela ou ao banco.
  [`email_sender.py:67`](../email_sender.py#L67)

**Interface do operador**

- Resultado persiste entre reruns; evita reemissão e reenvio em duplicata.
  [`app.py:865`](../app.py#L865)

- Planilha ilegível bloqueia a geração em vez de cair calada no textarea.
  [`app.py:636`](../app.py#L636)

- Reenvio: persiste o endereço digitado antes de tentar enviar.
  [`app.py:785`](../app.py#L785)

**Periféricos**

- Parser próprio por variável; reusar o de porta virava timeout de 465s.
  [`config.py:9`](../config.py#L9)

- Regressões da revisão viraram teste nomeado.
  [`test_email_sender.py:1`](../tests/test_email_sender.py#L1)

- Banco e nomes de arquivo cobertos separadamente.
  [`test_app_email.py:1`](../tests/test_app_email.py#L1)
