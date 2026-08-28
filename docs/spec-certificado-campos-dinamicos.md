---
title: 'Campos dinâmicos e perfis de certificado'
type: 'feature'
created: '2026-08-28'
status: 'done'
context: []
baseline_commit: '89862b6'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** O gerador só monta um layout: nome + parágrafo + QR em coordenadas fixas (`app.py:91-98`). Os certificados novos (convidado e palestrante da Oficina Regional) têm outro desenho e campos que hoje não existem — CPF, tema da palestra, horas da palestra — e não dá para posicioná-los sem editar código.

**Approach:** Cada modelo novo vira um **perfil**: um JSON em `perfis/` que aponta para um PDF e descreve cada campo (chave, x, y, tamanho, fonte, alinhamento) e o bloco de QR. Um engine novo (`perfil_certificado.py`) renderiza o PDF sobrepondo texto nos espaços em branco a partir do perfil + dados do participante. A planilha ganha `cpf`, `tema`, `horas`. A aba de geração ganha um seletor de modelo: os perfis novos com pré-visualização da 1ª página, e o fluxo atual da 9ª Conferência preservado como um ramo à parte. Posições se ajustam editando o JSON e conferindo no preview.

## Boundaries & Constraints

**Always:**
- Migração do SQLite aditiva e idempotente (`PRAGMA table_info` + `ALTER TABLE`), como `EMAIL_COLUMNS`. O `certificados.db` existente não pode ser recriado.
- Campo sem dado fica em branco; a geração **não** é bloqueada. A conferência da planilha mostra quem tem campo faltando.
- Perfil inválido (JSON malformado, PDF ausente, campo sem x/y) bloqueia a geração com erro claro — nunca lote parcial.
- Nos modelos novos o texto é **sobreposto** nos espaços em branco; nenhum parágrafo do PDF é reescrito ou coberto.
- Todo posicionamento (campos e QR) vem do perfil. Nenhuma coordenada de layout nova hardcoded em `app.py`.
- O fluxo atual da 9ª Conferência (upload de PDF + os 3 modos de texto) continua funcionando sem alteração, como um ramo selecionável ao lado dos perfis.
- E-mail, reenvio, login e código único intactos. `parse_planilha_destinatarios` mantém assinatura e testes.

**Ask First:**
- Adicionar dependência nova.
- Mudar o formato do perfil depois de aprovado.
- Exibir CPF em qualquer tela pública ou e-mail.

**Never:**
- Editor de posição na UI (inputs ou arrastar-e-soltar) e migração da 9ª Conferência para perfil — ambos em `deferred-work.md`.
- CPF na página pública nem em e-mail.
- Trocar SQLite, PyMuPDF ou o `smtplib` da stdlib.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Palestrante completo | Planilha nome+email+cpf+tema+horas, perfil palestrante | 4 campos sobrepostos nas posições do perfil; QR no ponto do perfil; linha no banco com cpf/tema/horas/perfil | N/A |
| Coluna opcional ausente | Perfil palestrante, planilha sem `tema` | Gera com o espaço do tema em branco; participante marcado "campos faltando" na conferência | Não é erro; não bloqueia lote |
| Perfil só-nome (convidado) | Planilha nome+email | Nome centralizado na régua; nenhum outro campo | N/A |
| Perfil inválido | Campo sem `x`/`y`, ou PDF inexistente | Erro nomeando perfil e campo; botão Gerar bloqueado; sem lote parcial | Validação antes do lote |
| Modelo legado selecionado | Opção "9ª Conferência (modelo atual)" | UI e comportamento idênticos aos de hoje | N/A |
| Linha maior que `largura_max` | Nome muito longo | Fonte reduz até `tamanho_min`; sem estourar o layout | Autosize |
| Sem planilha, perfil exige colunas | Perfil declara `colunas_obrigatorias` | Geração bloqueada pedindo a planilha; textarea segue válido para perfis só-nome | N/A |
| Consulta pública de palestrante | Código válido com tema/horas | Mostra "Tema" e "Carga horária da palestra"; **nunca** CPF | N/A |

</frozen-after-approval>

## Code Map

- `app.py` -- `render_generate_tab`, `make_zip`, `init_db`/`insert_certificate`/`EMAIL_COLUMNS`, `render_public_validation`; coords `app.py:91-98` e `insert_centered_text*` ficam só para o ramo legado.
- `email_sender.py` -- `parse_planilha_destinatarios`, `_localizar_colunas`, `_normalizar`, `NOME/EMAIL_ALIASES` — base do parser novo.
- `config.py` -- `OUTPUT_DIR`; defaults de evento migram para `metadados` do perfil.
- `perfil_certificado.py` -- NOVO: `carregar_perfil`, `validar_perfil`, `renderizar()`.
- `perfis/*.json`, `modelos/*.pdf` -- NOVOS: perfis convidado/palestrante e destino dos 2 PDFs de `novosModelos/`.
- `tests/test_perfil_certificado.py` -- NOVO; `test_email_sender.py` / `test_app_email.py` -- estender.

## Tasks & Acceptance

**Execution:**
- [x] `perfil_certificado.py` -- engine: `carregar_perfil`/`validar_perfil` (schema `id`, `nome`, `pdf`, `pagina`, `metadados`, `colunas_obrigatorias`, `qr`, `campos[]` com `campo/tipo/x/y/tamanho/tamanho_min/fonte/cor/alinhamento/maiusculas/largura_max`; `tipo` só `"linha"` neste escopo); `renderizar(perfil, dados, codigo, url_validacao)` sobrepõe cada `linha` no ponto âncora com autosize até `tamanho_min`, chave ausente em `dados` → pula o campo; insere QR (fundo branco + imagem + rótulo do código) na posição do `qr`. Nenhuma exceção vaza segredo. -- isola o layout; testável.
- [x] `perfis/` + `modelos/` -- mover os 2 PDFs; escrever 2 JSONs. Palestrante (`colunas_obrigatorias: [cpf, tema, horas]`): `nome` x≈345/y≈289 larg≈290, `cpf` x≈168/y≈317, `tema` x≈375/y≈376 larg≈320, `horas` x≈465/y≈405 centro. Convidado (só nome): `nome` centro x≈435/y≈315 larg≈480. `qr` por perfil, canto sem grafismo. `metadados`: evento, data_evento, carga_horaria, cidade, local, prefixo. -- layout fora do código.
- [x] `email_sender.py` -- `parse_planilha(arquivo) -> dict[str, dict]` (chaves internas `email`, `cpf`, `tema`, `horas`; email obrigatório como hoje; aliases sem acento: `cpf`=cpf/documento, `tema`=tema/temapalestra/titulo, `horas`=horas/horaspalestra/cargahorariapalestra). `parse_planilha_destinatarios` reimplementado como wrapper `{nome: d["email"]}`. Trava de colunas trocadas mantida. -- dados novos sem quebrar o parser atual.
- [x] `app.py` (banco) -- migração aditiva de `cpf TEXT`, `tema TEXT`, `horas TEXT`, `perfil TEXT`; `insert_certificate` e as linhas do registro/CSV carregam os campos. -- persistência.
- [x] `app.py` (aba gerar) -- `st.selectbox` de modelo: "9ª Conferência (modelo atual)" → ramo legado intacto; cada `perfis/*.json` → ramo novo: expander "Pré-visualizar 1ª página" renderiza um certificado de exemplo via engine (read-only); planilha obrigatória quando o perfil tem `colunas_obrigatorias`; `make_zip` chama `perfil_certificado.renderizar` com o dict do participante; `metadados` do perfil preenchem os defaults de evento/data/carga/cidade/local/prefixo (editáveis). -- um layout por perfil, sem tocar o fluxo atual.
- [x] `app.py` (página pública) -- `render_public_validation` exibe "Tema" e "Carga horária da palestra" quando `tema`/`horas` preenchidos; nunca renderiza `cpf`. -- reflete os campos sem vazar PII.
- [x] `tests/test_perfil_certificado.py` (+ estender email/app) -- cobrir cada linha da I/O Matrix. -- matriz coberta.

**Acceptance Criteria:**
- Given um perfil em `perfis/` apontando para um PDF válido, when o operador seleciona o perfil e gera o lote, then cada certificado é renderizado só a partir do perfil e do dict do participante, sem coordenada de layout lida de `app.py`.
- Given o `certificados.db` atual com registros, when `init_db()` roda (inclusive duas vezes), then `cpf/tema/horas/perfil` são adicionadas sem perder dados.
- Given a suíte atual (56 testes), when os testes rodam após a mudança, then 100% passam e o ramo legado da aba de geração se comporta como antes.
- Given um certificado de palestrante com CPF no banco, when alguém consulta o código na página pública, then o CPF não aparece em nenhuma parte da resposta.

## Design Notes

Formato de campo (perfil):
```json
{ "campo": "tema", "tipo": "linha", "x": 375, "y": 376, "tamanho": 11,
  "tamanho_min": 8, "fonte": "helv", "cor": [0.02, 0.13, 0.34],
  "alinhamento": "left", "maiusculas": false, "largura_max": 320 }
```
`renderizar()` recebe `dados = {"nome","cpf","tema","horas", ...}`; chave ausente → campo pulado (espaço em branco do PDF permanece).
Coordenadas medidas nos PDFs (`modelos/`): régua do nome do convidado em y≈319 (x 189-680); âncoras do palestrante sobre os sublinhados reais do parágrafo. Ambos os PDFs têm 869.7×623.8 pt.

**Normalização de nome** (`app.normalizar_nome_proprio`, aplicada na ingestão da planilha e do textarea, nos dois ramos): conserta a caixa de nomes que vêm TODOS em maiúsculas ou minúsculas (`VERA SUSANA La Falc` → `Vera Susana La Falc`) sem tocar em palavras de caixa mista já corretas (`William Xavier Oliveira` intacto). Partículas (`da`, `de`, `von`…) ficam minúsculas fora do início. O nome normalizado flui para o PDF, o banco, o nome do arquivo e o e-mail.

**Medição de largura no autosize**: `_largura_texto` usa `fitz.Font(nome).text_length()`, que bate exatamente com o render — `fitz.get_text_length` subestima ~5% em texto acentuado e deixava o tema longo passar por cima do "com duração de".

**Redesign do palestrante (2026-08-28)**: novo PDF com o tema numa linha própria de largura quase total (`tema` x≈147, largura_max 488, autosize até 6.5pt para os temas de ~120 chars) e `horas` no fim da mesma linha (x≈760). Página 870×624.

## Verification

**Commands:**
- `.venv/bin/python -m pytest -q` -- expected: todos passam (56 atuais + novos).
- `.venv/bin/python -c "import perfil_certificado, json, pathlib; [perfil_certificado.validar_perfil(json.loads(p.read_text())) for p in pathlib.Path('perfis').glob('*.json')]"` -- expected: sem exceção.

**Manual checks:**
- `streamlit run app.py`, logar, selecionar cada perfil novo, pré-visualizar a 1ª página e conferir que nome/CPF/tema/horas caem sobre os espaços em branco; gerar um lote de 2 e abrir o ZIP.
- Selecionar "9ª Conferência (modelo atual)" e confirmar que a aba está idêntica à de hoje.

## Suggested Review Order

**Engine de perfis (o coração da mudança)**

- Entrada: como um perfil + dados viram um PDF; nenhuma coordenada fora daqui.
  [`perfil_certificado.py:238`](../../perfil_certificado.py#L238)
- Validação do perfil, incluindo abrir o PDF — perfil inválido nunca chega ao lote.
  [`perfil_certificado.py:55`](../../perfil_certificado.py#L55)
- Desenho de um campo `linha`: âncora, alinhamento, autosize até `tamanho_min`.
  [`perfil_certificado.py:185`](../../perfil_certificado.py#L185)
- Os dois perfis reais e as coordenadas medidas nos PDFs.
  [`oficina-palestrante.json:1`](../../perfis/oficina-palestrante.json#L1)
  [`oficina-convidado.json:1`](../../perfis/oficina-convidado.json#L1)

**Planilha estendida**

- `parse_planilha` devolve `{nome: {email, cpf, tema, horas}}`; wrapper mantém a API antiga.
  [`email_sender.py:177`](../../email_sender.py#L177)
- Mapa de colunas com aliases sem acento para cpf/tema/horas.
  [`email_sender.py:103`](../../email_sender.py#L103)

**Persistência**

- Migração aditiva `cpf/tema/horas/perfil` reusando o padrão de `EMAIL_COLUMNS`.
  [`app.py:116`](../../app.py#L116)
- `insert_certificate` carrega os campos novos.
  [`app.py:186`](../../app.py#L186)

**Geração e UI**

- `make_zip` agora recebe `render_fn` + `participantes`; grava cpf/tema/horas/perfil na linha.
  [`app.py:497`](../../app.py#L497)
- As duas `render_fn`: legado (template + parágrafo) e por perfil.
  [`app.py:467`](../../app.py#L467)
- Pré-voo: renderiza um certificado descartável antes de tocar banco/disco.
  [`app.py:825`](../../app.py#L825)
- Seletor de modelo despacha para o ramo legado (intacto) ou o ramo de perfil.
  [`app.py:858`](../../app.py#L858)
- Ramo de perfil: preview da 1ª página, planilha obrigatória, conferência de campos faltando.
  [`app.py:986`](../../app.py#L986)

**Consulta pública (sem PII)**

- `campos_publicos_extra` mostra tema/horas e nunca CPF.
  [`app.py:750`](../../app.py#L750)

**Testes**

- Engine: matriz de I/O, validação, autosize, perfis reais.
  [`test_perfil_certificado.py:1`](../../tests/test_perfil_certificado.py#L1)
- Geração por perfil + ramo legado + consulta pública sem CPF.
  [`test_app_perfil.py:1`](../../tests/test_app_perfil.py#L1)
- Colunas extras da planilha e wrapper preservado.
  [`test_email_sender.py:46`](../../tests/test_email_sender.py#L46)
