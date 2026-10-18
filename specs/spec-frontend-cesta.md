# Spec de Frontend — Cesta (agente de compras)

Versão 1 · 18/10/2026 · baseada no canvas "Telas – Agente de Compras"

Foco deste documento: **identidade visual** e **comportamento mobile first**. Lógica do agente e integração com o MCP ficam em outro spec.

---

## 1. Princípios

1. **Mobile first de verdade.** Tudo é desenhado para 390 px de largura. Telas maiores só centralizam a coluna; nada é "esticado".
2. **O cliente escolhe o produto, o agente escolhe o resto.** Nas telas de seleção não aparece preço nem loja. Preço e loja só aparecem depois que o carrinho é montado (Chat e Carrinho).
3. **Vermelho é ação.** O vermelho principal é reservado para o que o usuário pode tocar para avançar (botão principal, botão flutuante, "+", item selecionado). Não usar vermelho como decoração.
4. **Uma ação principal por tela**, sempre no rodapé, ao alcance do polegar.
5. **Nada é enviado sem confirmação.** A tela de Carrinho é o único lugar onde o pedido é finalizado.

---

## 2. Tokens de design

### 2.1 Cores

| Token | Hex | Uso |
|---|---|---|
| `--color-primary` | `#D61F2C` | Botões principais, FAB, botão "+", bordas de seleção, foco, ícone ativo |
| `--color-primary-strong` | `#A3121D` | Texto vermelho sobre fundo claro, links, hover/pressed do primário |
| `--color-primary-tint` | `#FDE8EA` | Fundo das etiquetas "Com restrição", fundo de ícone em opção selecionada |
| `--color-primary-soft` | `#FFF5F6` | Fundo de cartão/opção selecionada, barra de progresso do chat |
| `--color-primary-track` | `#F8CDD1` | Trilho da barra de progresso |
| `--color-ink` | `#1F1F1F` | Texto principal, ícones, botão escuro secundário |
| `--color-ink-2` | `#4A4A4A` | Texto secundário forte, quantidades, legendas de seção |
| `--color-muted` | `#6B6B6B` | Subtítulos, metadados, placeholders |
| `--color-bg` | `#F7F7F7` | Fundo das telas de lista (Início, Lista, Carrinho, Chat) |
| `--color-surface` | `#FFFFFF` | Cartões, folhas (sheets), telas de formulário |
| `--color-control` | `#F0F0F0` | Fundo de botões redondos de ícone (voltar, fechar, mais) |
| `--color-border` | `#EAEAEA` | Borda de cartões e opções não selecionadas |
| `--color-divider` | `#F2F2F2` | Linhas entre itens de lista |
| `--color-outline` | `#DCDCDC` | Borda de chips e controles neutros |
| `--color-dashed` | `#B5B5B5` | Borda tracejada de "Adicionar item", setas de navegação |
| `--color-warn` | `#A04A07` | Rótulos de conflito no chat ("Item esgotado") |
| `--color-warn-strong` | `#7A3605` | Texto do aviso de troca no carrinho |
| `--color-warn-bg` | `#FFF6EC` | Fundo do aviso de troca no carrinho |
| `--color-overlay` | `rgba(0, 0, 0, 0.55)` | Fundo atrás de folhas (sheets) |

**Cores de placeholder de produto** (fundo das miniaturas enquanto não há foto): `#F3EBDD`, `#E6EEF6`, `#F6E6E3`, `#E9F1E4`, `#EAE7F3`, `#EEECE8`. Escolher pela categoria do produto, nunca aleatoriamente a cada render.

**Contraste (WCAG AA)**

| Combinação | Razão | Uso permitido |
|---|---|---|
| Branco sobre `#D61F2C` | 5,1 : 1 | Qualquer texto |
| `#A3121D` sobre `#FDE8EA` | ≈ 6,8 : 1 | Qualquer texto |
| `#6B6B6B` sobre `#FFFFFF` | 5,3 : 1 | Qualquer texto |
| `#6B6B6B` sobre `#F7F7F7` | ≈ 5,0 : 1 | Qualquer texto |
| `#D61F2C` sobre `#FFFFFF` | 5,1 : 1 | Texto e ícones |

> Não usar o vermelho mais claro do iFood (`#EA1D2C`) com texto branco pequeno: fica abaixo de 4,5 : 1.

### 2.2 Tipografia

Fonte única: **Plus Jakarta Sans** (Google Fonts), pesos 400, 500, 600, 700 e 800.

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
```

| Token | Tamanho / peso / altura | Letter-spacing | Uso |
|---|---|---|---|
| `display` | 28 / 800 / 1.15 | -0.02em | Título da tela inicial, título de folhas |
| `title-xl` | 22 / 800 / 1.2 | -0.02em | Título de tela (Lista, Item) |
| `title-lg` | 20 / 800 / 1.25 | 0 | Título de tela secundária (Chat, Carrinho) |
| `title-md` | 16–17 / 700 / 1.3 | 0 | Nome de loja, texto de botão principal, títulos de cartão |
| `body-strong` | 15 / 600 / 1.35 | 0 | Nome de item em lista, nome de produto |
| `body` | 14 / 400 / 1.45 | 0 | Mensagens do chat, descrições |
| `caption` | 13 / 400–600 / 1.4 | 0 | Subtítulos e metadados |
| `small` | 12 / 600–700 / 1.35 | 0 | Rótulos de valores, dicas abaixo de campos |
| `micro` | 11 / 700–800 / 1.2 | 0 | Etiquetas e selos ("Com restrição", "Prefere") |
| `overline` | 13 / 700 / 1.2 | 0.06em, MAIÚSCULAS | Rótulos de seção ("PRÓXIMA COMPRA") |

Regras:
- **Campos de texto usam no mínimo 16 px**, senão o iOS dá zoom ao focar. (O campo de restrição está em 15 px no desenho: subir para 16.)
- Números de preço e total usam peso 800.
- Respeitar o tamanho de fonte do sistema: usar `rem` em código (`1rem = 16px`) e testar com fonte do sistema em 130 %.

### 2.3 Espaçamento

Grade de **4 px**. Valores usados: `4, 6, 8, 10, 12, 14, 16, 20, 24, 28`.

| Token | Valor | Uso |
|---|---|---|
| `--space-gutter` | 20 px | Margem lateral padrão das telas |
| `--space-gutter-dense` | 16 px | Telas com grade de produtos (Adicionar itens) |
| `--space-card` | 16–18 px | Padding interno de cartões |
| `--space-row` | 12 × 14 px | Padding de linha de item (vertical × horizontal) |
| `--space-stack` | 8–16 px | Distância entre blocos na mesma tela |
| `--space-top` | 24 px | Topo da tela até o cabeçalho (antes da safe area) |
| `--space-bottom` | 28 px | Rodapé até a borda (antes da safe area) |

### 2.4 Cantos arredondados

| Token | Valor | Uso |
|---|---|---|
| `--radius-xs` | 6–8 px | Selos de texto ("Prefere", "Exige") |
| `--radius-sm` | 10–12 px | Miniaturas de produto, logo de loja |
| `--radius-md` | 14 px | Caixas de aviso, botão "Adicionar item" |
| `--radius-lg` | 16 px | Opções selecionáveis, campo de texto, cartões de produto |
| `--radius-xl` | 18 px | Cartões de lista, botão principal |
| `--radius-2xl` | 20 px | Cartão de destaque da tela inicial |
| `--radius-sheet` | 28 px | Topo das folhas (sheets) |
| `--radius-pill` | 999 px | Botões redondos, FAB, chips, barra de busca |

### 2.5 Sombras

Só dois níveis; o resto do app é plano, separado por bordas.

| Token | Valor | Uso |
|---|---|---|
| `--shadow-fab` | `0 6px 18px rgba(214, 31, 44, 0.30)` | Botão flutuante vermelho |
| `--shadow-raised` | `0 1px 3px rgba(31, 31, 31, 0.12)` | Elemento elevado sobre fundo cinza |

### 2.6 Ícones

- Estilo **traço (outline)**, pontas e junções arredondadas, espessura 2–2.4.
- Biblioteca sugerida: **Lucide** (`lucide-react`), que tem o mesmo estilo dos desenhos.
- Tamanhos: 20 px dentro de botão de 44 px; 18 px em linhas e chips; 12–13 px dentro de etiquetas.
- Ícones decorativos com `aria-hidden="true"`; botões só com ícone sempre com `aria-label`.
- Não usar emoji na interface.

---

## 3. Mobile first: layout e comportamento

### 3.1 Larguras

| Faixa | Comportamento |
|---|---|
| 320–359 px | Suportar sem quebrar: textos quebram linha, grade de produtos continua em 2 colunas, botões principais mantêm 56 px de altura |
| **360–430 px** | **Alvo principal** (desenho feito em 390 × 844) |
| ≥ 600 px | Conteúdo centralizado numa coluna de **480 px**; fundo `--color-bg` ao redor. Sem layout de duas colunas |

Escrever o CSS para o celular primeiro e usar `@media (min-width: 600px)` só para centralizar.

### 3.2 Estrutura de toda tela

```
┌──────────────────────────────┐  ← safe-area-inset-top
│ Cabeçalho (fixo, sem rolar)  │
├──────────────────────────────┤
│                              │
│ Conteúdo (rola)              │
│                              │
├──────────────────────────────┤
│ Rodapé de ação (fixo)        │  ← safe-area-inset-bottom
└──────────────────────────────┘
```

- Usar `100dvh` (não `100vh`) para a altura da tela, por causa da barra do navegador no celular.
- Respeitar a área segura: `padding-bottom: calc(28px + env(safe-area-inset-bottom))` no rodapé e `viewport-fit=cover` no `<meta name="viewport">`.
- Só o miolo rola. Cabeçalho e rodapé ficam presos.
- Quando houver FAB, o conteúdo ganha `padding-bottom` igual à altura do FAB + 16 px, para o último item não ficar escondido atrás dele.

### 3.3 Toque e alcance

- **Alvo mínimo de toque: 44 × 44 px.** Inclusive ícones pequenos (o ícone é pequeno, a área de toque não).
- Ações principais sempre na **metade de baixo** da tela (rodapé ou FAB à direita).
- Linhas de lista inteiras são tocáveis, com seta `›` à direita.
- Não depender de hover: todo estado de hover precisa também ter estado de pressionado (`:active`) e foco visível (`:focus-visible` com contorno de 2 px em `--color-primary`).

### 3.4 Teclado virtual

- Ao focar um campo, o rodapé de ação sobe junto com o teclado (ou some, se o campo estiver no rodapé, como no chat).
- O campo de busca e o do chat abrem o teclado com `enterkeyhint="search"` e `enterkeyhint="send"`.

### 3.5 Movimento

- Transições curtas: 150–200 ms, `ease-out`.
- Folhas (sheets) sobem de baixo; telas novas entram da direita.
- Respeitar `prefers-reduced-motion`: trocar deslizamentos por fade simples.

---

## 4. Componentes

Para cada componente: tamanho, cores e estados.

### 4.1 Botão principal
- Altura **56 px**, largura total, canto `--radius-xl`, texto `title-md` branco, fundo `--color-primary`.
- Pressionado: fundo `--color-primary-strong`.
- Desabilitado: fundo `#EAEAEA`, texto `--color-muted`.
- Carregando: texto troca por indicador giratório branco; botão mantém a largura.

### 4.2 Botão flutuante (FAB)
- Altura **56–60 px**, pílula, padding lateral 22–24 px, ícone 20 px + texto 16/700.
- Fundo `--color-primary`, sombra `--shadow-fab`.
- Posição: 20 px da direita e 28 px do fundo (+ safe area). Na tela inicial fica acima da barra de navegação.
- Exemplos: "Nova lista" (Início), "Montar carrinho" (Lista).

### 4.3 Botão redondo de ícone
- **44 × 44 px**, círculo, fundo `--color-control`, ícone 18–20 px `--color-ink`.
- Usos: voltar, fechar, mais opções, remover item, limpar busca.

### 4.4 Botão "+" do cartão de produto
- 44 × 44 px, círculo vermelho, borda branca de 3 px, ícone "+" branco.
- Fica sobre o canto superior direito da miniatura (deslocado -8 px para cima e -6 px para a direita).
- Depois de adicionado, vira um círculo `--color-ink` com o número de unidades.

### 4.5 Linha de item da lista
- Altura mínima **64 px**, padding 12 × 14 px.
- Miniatura 40 × 40 (`--radius-sm`) · nome (`body-strong`) · quantidade (`14/700`, `--color-ink-2`) · seta `›` (16 px, `--color-dashed`).
- Se o item tiver restrição: etiqueta **"Com restrição"** abaixo do nome.
- Linhas separadas por `--color-divider`; o grupo fica num cartão branco com `--radius-xl`.

### 4.6 Etiqueta "Com restrição"
- Texto `micro` em `--color-primary-strong`, fundo `--color-primary-tint`, pílula, padding 3 × 8 px, ícone de linhas 12 px à esquerda.
- **Nunca mostrar o texto da restrição na lista**, só a etiqueta. O texto completo aparece ao tocar no item.

### 4.7 Selos de regra ("Como o agente entendeu")
| Selo | Fundo | Texto |
|---|---|---|
| Prefere | `--color-primary` | branco |
| Exige | `--color-ink` | branco |
| Se faltar | `#EAEAEA` | `--color-ink-2` |
| Nunca | `#EAEAEA` | `--color-ink-2` |

Texto `micro` 800, canto 6 px, sem quebra de linha.

### 4.8 Campo de texto grande (restrição)
- Largura total, borda 2 px `--color-primary` quando focado (1 px `--color-outline` quando não), canto `--radius-lg`, padding 12 × 14 px.
- Texto 16 px, altura de linha 1.5. Cresce até 8 linhas e depois rola por dentro.
- Rótulo acima em `title-md`; à direita do rótulo, nota "vale só nesta lista" em `small`.

### 4.9 Barra de busca
- Pílula com fundo `--color-bg`, borda `--color-border`, padding 6 px.
- Botão voltar (esquerda) · campo 17 px · botão limpar (direita).

### 4.10 Cartão de produto (busca)
- Grade de **2 colunas**, espaço 22 px vertical × 14 px horizontal.
- Miniatura 140 px de altura, `--radius-lg`, fundo de placeholder.
- Abaixo: nome (`body-strong`) e tamanho (`caption`, `--color-muted`).
- **Sem preço, sem loja, sem desconto.**

### 4.11 Opção selecionável (radio em cartão)
- Padding 14 px, `--radius-lg`.
- Não selecionada: borda 1 px `--color-border`.
- Selecionada: borda 2 px `--color-primary` e fundo `--color-primary-soft`.
- O `<input type="radio">` real fica à direita, com `accent-color: var(--color-primary)`.

### 4.12 Balões do chat
| | Agente | Cliente |
|---|---|---|
| Alinhamento | esquerda | direita |
| Fundo | `--color-surface` + borda `--color-border` | `--color-primary` |
| Texto | `--color-ink`, 14/400 | branco, 14/600 |
| Cantos | 18 18 18 **6** | 18 18 **6** 18 |
| Largura máx. | 320 px | 260 px |

- Respostas rápidas abaixo do balão do agente: chips de 44 px de altura; a opção sugerida tem borda 2 px vermelha e texto `--color-primary-strong`.
- Rótulo do conflito dentro do balão em `--color-warn` 700 ("1 · Item esgotado.").
- Lista de mensagens com `aria-live="polite"`.

### 4.13 Barra de progresso (montagem do carrinho)
- Faixa sob o cabeçalho do chat, fundo `--color-primary-soft`.
- Trilho 6 px `--color-primary-track`, preenchimento `--color-primary`, texto à direita "16 de 18 itens prontos" em `small` `--color-primary-strong`.

### 4.14 Cartão de loja (carrinho)
- Logo 40 × 40 (`--radius-sm`) com as iniciais em branco · nome da loja e número de itens (16/700) · horário (`caption`) · subtotal (16/800) à direita.
- Abaixo: resumo dos itens em uma linha (`caption`), cortado com "e mais N".
- Troca feita pelo agente: caixa `--color-warn-bg` com ícone de setas e texto `--color-warn-strong`.

### 4.15 Barra de navegação inferior (só na tela inicial)
- Altura 84 px incluindo safe area, fundo branco, borda superior `--color-border`.
- 3 abas: Início, Pedidos, Perfil. Ícone 22 px + rótulo 12 px.
- Ativa: `--color-primary` 700 com `aria-current="page"`. Inativa: `--color-muted` 600.

---

## 5. Telas

Mapa de navegação:

```
Início ──► Lista ──► Item e restrições
             │
             ├──► Adicionar itens ──► (volta para Lista)
             │
             └──► [Montar carrinho] ──► Chat ──► Carrinho ──► Confirmar
```

### 5.1 Início
- **Cabeçalho:** logo (36 px, quadrado vermelho com cesta) + nome "Cesta" (22/800) · botão de notificações · chip de endereço · título `display` "O que vamos comprar hoje?".
- **Próxima compra:** cartão vermelho (`--color-primary`, `--radius-2xl`) com nome da lista, recorrência, selo branco "Em N dias" e rodapé com "Com restrição: N itens" e "Último total".
- **Suas listas:** cartões brancos com ícone colorido 48 px, nome, "N itens · recorrência" e seta.
- **FAB** "Nova lista" acima da barra de navegação.
- **Estado vazio:** ilustração simples + "Crie sua primeira lista" + botão principal.

### 5.2 Itens da lista
- **Cabeçalho:** voltar · nome da lista (`title-xl`) e "N itens · M com restrição" · mais opções.
- **Conteúdo:** botão tracejado "Adicionar item" (52 px) · cartão com as linhas de item (4.5) · "Ver mais N itens".
- **FAB** "Montar carrinho" no canto inferior direito.
- **Estado vazio:** só o botão "Adicionar item" e uma frase curta; FAB desabilitado.

### 5.3 Item e restrições
- Tela cheia (não folha), porque a restrição pode ser longa.
- **Cabeçalho:** voltar · nome da lista em `caption` · remover item.
- **Conteúdo:** miniatura 64 px + nome do item · controle de quantidade (− 12 +) · campo de restrição (4.8) · bloco "Como o agente entendeu" com os selos (4.7).
- **Rodapé:** botão principal "Salvar item".
- O bloco "Como o agente entendeu" atualiza alguns segundos depois que o cliente para de digitar; enquanto isso, mostra um carregamento discreto.

### 5.4 Adicionar itens
- **Cabeçalho:** barra de busca · "Adicionando à lista **Compra mensal**".
- **Conteúdo:** grade de produtos (4.10) · caixa de aviso: "Você escolhe o produto. Loja, marca e preço o agente define ao montar o carrinho, seguindo as restrições de cada item."
- **Rodapé:** botão principal "Adicionar à lista (N itens)"; desabilitado com 0 itens.

### 5.5 Se estiver esgotado *(a decidir)*
- Hoje nenhuma tela leva até ela: o campo "Se faltar" da restrição cobre o mesmo caso. Manter só se o grupo quiser uma forma guiada, sem texto livre.

### 5.6 Chat
- **Cabeçalho:** voltar · "Assistente de compras" + nome da lista.
- Barra de progresso (4.13).
- **Conteúdo:** fundo `--color-bg`, balões (4.12), um conflito por vez.
- **Rodapé:** botão escuro "Ver carrinho · R$ total" (48 px, `--color-ink`) + campo de mensagem em pílula com botão enviar vermelho.
- O botão "Ver carrinho" só fica ativo quando todos os conflitos estiverem resolvidos.

### 5.7 Carrinho
- **Cabeçalho:** voltar · "Confirme sua compra" + lista e número de itens.
- **Conteúdo:** um cartão por loja (4.14) · endereço e pagamento (linhas de 52 px com "Trocar") · resumo de valores com total em 22/800.
- **Rodapé:** botão principal "Confirmar e pagar R$ total" + link "Pedir mudança ao assistente".
- Ao tocar em confirmar: botão entra em carregamento e não pode ser tocado de novo (evita pedido duplicado).

---

## 6. Estados globais

| Estado | Como aparece |
|---|---|
| Carregando lista ou tela | Esqueletos cinza `--color-control` no formato do conteúdo (não spinner de tela cheia) |
| Agente montando carrinho | Chat com a barra de progresso avançando e mensagens chegando |
| Erro de rede | Faixa no topo com texto `--color-ink` sobre `--color-warn-bg` e botão "Tentar de novo" |
| Ação concluída | Toast no rodapé, fundo `--color-ink`, texto branco, 3 s |

---

## 7. Acessibilidade (checklist)

- [ ] Toda ação é `<button>` ou `<a href>` de verdade (nunca `div` com clique).
- [ ] Botões só com ícone têm `aria-label`.
- [ ] Rádios e campos têm `<label>` ligado.
- [ ] Foco visível em todos os elementos tocáveis.
- [ ] Contrastes da seção 2.1 respeitados.
- [ ] Testado com fonte do sistema em 130 % e com leitor de tela (TalkBack ou VoiceOver) nas telas Lista e Carrinho.

---

## 8. Código inicial

### 8.1 Variáveis CSS

```css
:root {
  /* cores */
  --color-primary: #D61F2C;
  --color-primary-strong: #A3121D;
  --color-primary-tint: #FDE8EA;
  --color-primary-soft: #FFF5F6;
  --color-primary-track: #F8CDD1;
  --color-ink: #1F1F1F;
  --color-ink-2: #4A4A4A;
  --color-muted: #6B6B6B;
  --color-bg: #F7F7F7;
  --color-surface: #FFFFFF;
  --color-control: #F0F0F0;
  --color-border: #EAEAEA;
  --color-divider: #F2F2F2;
  --color-outline: #DCDCDC;
  --color-dashed: #B5B5B5;
  --color-warn: #A04A07;
  --color-warn-strong: #7A3605;
  --color-warn-bg: #FFF6EC;
  --color-overlay: rgba(0, 0, 0, 0.55);

  /* tipografia */
  --font-sans: "Plus Jakarta Sans", system-ui, sans-serif;

  /* espaçamento */
  --space-gutter: 20px;
  --space-gutter-dense: 16px;

  /* cantos */
  --radius-sm: 12px;
  --radius-md: 14px;
  --radius-lg: 16px;
  --radius-xl: 18px;
  --radius-2xl: 20px;
  --radius-sheet: 28px;
  --radius-pill: 999px;

  /* sombras */
  --shadow-fab: 0 6px 18px rgba(214, 31, 44, 0.30);
  --shadow-raised: 0 1px 3px rgba(31, 31, 31, 0.12);

  /* tamanhos */
  --tap: 44px;
  --btn-primary: 56px;
}

html { font-family: var(--font-sans); color: var(--color-ink); background: var(--color-bg); }
body { margin: 0; min-height: 100dvh; }

.app { max-width: 480px; margin: 0 auto; min-height: 100dvh; background: var(--color-bg); }

.footer-action {
  padding: 16px var(--space-gutter) calc(28px + env(safe-area-inset-bottom));
}
```

### 8.2 Tailwind (se usarem)

```js
// tailwind.config.js
export default {
  theme: {
    extend: {
      colors: {
        primary: { DEFAULT: "#D61F2C", strong: "#A3121D", tint: "#FDE8EA", soft: "#FFF5F6", track: "#F8CDD1" },
        ink: { DEFAULT: "#1F1F1F", 2: "#4A4A4A", muted: "#6B6B6B" },
        bg: "#F7F7F7",
        control: "#F0F0F0",
        line: { DEFAULT: "#EAEAEA", divider: "#F2F2F2", outline: "#DCDCDC", dashed: "#B5B5B5" },
        warn: { DEFAULT: "#A04A07", strong: "#7A3605", bg: "#FFF6EC" },
      },
      fontFamily: { sans: ['"Plus Jakarta Sans"', "system-ui", "sans-serif"] },
      borderRadius: { sm: "12px", md: "14px", lg: "16px", xl: "18px", "2xl": "20px", sheet: "28px" },
      boxShadow: { fab: "0 6px 18px rgba(214, 31, 44, 0.30)", raised: "0 1px 3px rgba(31, 31, 31, 0.12)" },
      spacing: { tap: "44px", gutter: "20px" },
    },
  },
};
```

### 8.3 Viewport

```html
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#FFFFFF">
```

---

## 9. Pendências de design

1. Tela "Se estiver esgotado": manter ou remover (ver 5.5).
2. Frase de economia no carrinho ("R$ X a menos que tudo numa loja só"): manter ou remover.
3. "Último total" na tela inicial: manter preço ou tirar.
4. Modo escuro: não desenhado; fora do escopo do hackathon.
5. Fotos reais de produto: hoje são placeholders coloridos.
