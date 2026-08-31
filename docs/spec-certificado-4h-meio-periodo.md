# Certificado de 4h para quem fez só um período (Oficina Regional de Vigilância Sanitária)

**Data:** 2026-08-31
**Origem:** pedido do operador — a planilha de check-ins tem a coluna `Grupo` com
`Manhã e tarde`, `Só manhã` e `Só tarde`. Quem fez só um período recebe 4h; quem
fez o dia todo recebe 8h.

## O quê

- Novo modelo `modelos/oficina-convidado-4h.pdf` (mesmo desenho do convidado, com
  "carga horária de 4 horas" já impressa).
- Novo perfil `perfis/oficina-convidado-4h.json` — cópia do convidado apontando
  para o PDF de 4h, `carga_horaria: "4 horas"` e `filtro_grupo: ["Só manhã", "Só tarde"]`.
- Perfil `oficina-convidado` (8h) ganhou `filtro_grupo: ["Manhã e tarde"]`.

## Como o filtro funciona

- `perfil_certificado.filtro_grupo(perfil)` / `grupo_aceito(perfil, valor)`:
  compara normalizando acento, caixa e separadores (`Só manhã` → `somanha`).
  Perfil sem `filtro_grupo` aceita qualquer valor.
- `email_sender.COLUNAS_EXTRAS` agora inclui `grupo` (aliases: `grupo`, `periodo`,
  `turno`). `parse_planilha` passa a devolver `grupo` em cada registro.
- `NOME_ALIASES` ganhou `pessoa` — cabeçalho usado na planilha de check-ins.
- `app.filtrar_participantes_por_grupo(perfil, participantes)` → `(mantidos, relatorio)`.
  Se a planilha não traz `grupo` preenchido não há como filtrar: mantém todos e o
  `relatorio["tem_coluna"]` fica `False` para a UI avisar.
- Na aba Gerar: ao subir a planilha num perfil com `filtro_grupo`, a UI mostra
  quantos nomes se encaixam, um expander com os ignorados (nome + grupo) e bloqueia
  a geração se sobrar ninguém.

## Fluxo do operador

Sobe a **mesma** planilha completa duas vezes: uma no perfil de 8h (emite os
`Manhã e tarde`), outra no de 4h (emite os `Só manhã` / `Só tarde`).

## Validação (planilha real, 125 linhas)

- 8h → 112 certificados, 13 ignorados.
- 4h → 13 certificados (10 só manhã + 3 só tarde), 112 ignorados.
