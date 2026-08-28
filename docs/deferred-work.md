# Trabalho adiado

## Editor visual de posicionamento (arrastar-e-soltar)

**Origem:** Split do escopo em `spec-certificado-campos-dinamicos.md` (Objetivo B).
**Data:** 2026-08-28

**O quê:** Componente de canvas sobre a 1ª página renderizada do PDF permitindo
posicionar cada campo do perfil com o mouse (arrastar, redimensionar) e salvar as
coordenadas de volta no arquivo de perfil JSON.

**Depende de:** engine orientado a campos + formato de perfil estável (Objetivo A,
entregue em `spec-certificado-campos-dinamicos.md`).

**Notas técnicas:**
- Exige dependência nova: `streamlit-drawable-canvas` ou componente Streamlit custom.
- Deve ler/gravar o mesmo formato de perfil (`perfis/*.json`) que o Objetivo A define.
- Conversão de coordenadas tela (px) <-> PDF (pt) considerando o zoom do preview.

---

## Migrar a 9ª Conferência para o sistema de perfis

**Origem:** Split de `spec-certificado-campos-dinamicos.md` (token count).
**Data:** 2026-08-28

**O quê:** Substituir o fluxo legado (upload de PDF + `st.radio` de 3 modos de
texto + `insert_textbox`/`BODY_COVER_RECT` em `app.py`) por um perfil
`conferencia-9.json`. Exige um tipo de campo `paragrafo` no engine: `rect` +
`cobrir` (bbox pintado de branco antes) + textbox com wrap/centro/autosize, e
presets de texto (participação / equipe / personalizado) escolhidos na UI.

**Depende de:** engine de perfis com campo `linha` (Objetivo A).

**Enquanto não for feito:** o ramo legado da aba de geração continua funcionando
exatamente como hoje, selecionável ao lado dos perfis novos.

---

## Ajuste de posição na UI (inputs numéricos)

**Origem:** Split de `spec-certificado-campos-dinamicos.md` (token count).
**Data:** 2026-08-28

**O quê:** No expander de preview, `st.number_input` de x/y/tamanho por campo (e
QR), re-render ao vivo e botão "Salvar posições no perfil" com escrita atômica do
JSON. Degrau intermediário antes do editor visual (Objetivo B).

**Depende de:** engine de perfis + preview da 1ª página (Objetivo A).

**Enquanto não for feito:** posições se ajustam editando `perfis/<id>.json` à mão
e conferindo no preview read-only.
