# Registro de campanha — modelo não executado

Plano: [test_plan_efficiency.md](test_plan_efficiency.md). Copiar para a campanha real; não preencher resultados com estimativas.

Este modelo preserva o registro original e acrescenta rastreabilidade por IDs. Ciclo: **HIP → cenário/variante → código → executor → RUN → evidência/resultado → PROB → FIX → REV**. Relações podem ser muitos-para-muitos. Campos de execução não preenchidos significam não executado. Commit documental, commit do código medido e commit do executor são identificações distintas.

## Identificação e reprodução

- Campanha, responsável, data/fuso, IDs/variantes/prioridade/estado:
- SHA API e Flutter; baseline ou candidato:
- Ambiente E1–E5; OS/CPU/RAM/recursos Docker/processos/workers:
- Celular/OS/display/modo/temperatura/bateria/brilho:
- Configuração sanitizada/schema/dependências/modelo/checkpoint/prompt:
- Fixtures/seed/hashes/snapshot inicial/efeitos esperados:
- Rede observada/proxy/ponto e instante da falha:
- Comando/script exato, versão da ferramenta e diretório:
- Aquecimento/duração/n/concorrência/taxa oferecida/método de percentil:
- Pré-requisitos não atendidos, skips e orçamento/chamadas externas:

### Manifesto de versões e comparação

| Papel/braço | Repositório | Ref de origem | SHA completo avaliado | Schema/runtime/lock/modelo | Estado da cópia |
|---|---|---|---|---|---|
| A oficial | API | main | A preencher | — | — |
| A oficial | Flutter | main | A preencher | — | — |
| B refatorada | API | code-quality-cleanup | A preencher | — | — |
| B refatorada | Flutter | code-quality-cleanup | A preencher | — | — |
| Candidato futuro, se houver | API/Flutter em linhas separadas | A preencher | A preencher | — | — |

- ID da campanha, versão/commit deste plano e responsável pela revisão:
- BR-01/02/03/04/05 aplicáveis; pacote completo ou braço com API/cliente fixo:
- SHAs baseline OPT-0 aceitos (API e Flutter), aprovação/data e campanha A/B de origem:
- Dados/configuração/hardware/rede equivalentes, hashes de snapshot e restauração por rodada:
- Divergências de schema/dependência/modelo, incompatibilidades e limite de atribuição:
- Ordem A/B por bloco, condições térmicas/cache e interferências externas:
- main preservada; diretórios isolados/SHAs, sem checkout ou modificação da branch oficial:

### Mapa de hipótese, cenário, código e executor

| HIP-ID e hipótese verificável | Cenário/variante | Projeto/arquivo/símbolo/linhas no SHA | Commit/link imutável do código | Teste/node ID, script ou roteiro | SHA/hash do executor e comando exato | RUN-IDs |
|---|---|---|---|---|---|---|
| A preencher | — | — | — | — | — | — |

Se o executor não existir, marcar PLANEJADO e registrar localização proposta separadamente. Inspeção manual também exige roteiro/versão, ferramenta e passos. Caminho local ou link de branch móvel sozinho não identifica o código histórico.

### Plano estatístico prévio à coleta

- Métrica primária/secundárias, unidade independente, estratos e menor diferença relevante:
- Precisão-alvo/nível de IC e justificativa, piloto usado somente para dimensionamento:
- n proposto/efetivo por estrato, número de gatos/sessões/fotos/pares/requests conforme caso:
- Repetições por foto/sessão/rodada e motivo; suporte esperado na cauda P95/P99:
- Aquecimento/critério de estabilidade/teto; separação frio/quente/cache hit/miss:
- Método de IC (Wilson/exato/ordem/bootstrap por cluster/bloco pareado), dependência e seed:
- Orçamento de tempo/custo/amostras, regra de parada fixada e exclusões pré-definidas:
- Prevalência/ponderação/galeria/splits e auditoria de rótulos/duplicatas para REID:
- Limiares: atuais, grade/calibração, ponto operacional congelado e holdout não usado em tuning:
- Comparações exploratórias/múltiplas e validação independente prevista:
- Quem aprovou o desenho e quando; alterações posteriores com justificativa e impacto:

## Evidências por rodada

| Rodada/variante | n sucessos/erros/timeouts | P50/P95/P99 ms quando válidos | RPS oferecido/concluído | Bytes/requests/SQL | CPU/RAM/pool/fila | Artefato bruto |
|---|---|---|---|---|---|---|
| A preencher | — | — | — | — | — | — |

- Duração dos erros, timeouts censurados e cancelamentos:
- Frio/quente, cache hit/miss e etapas paralelas:
- Dispersão/intervalo entre rodadas e limitações estatísticas:
- Overhead da instrumentação, gerador saturado, interrupção:

### Índice de execuções e evidências

| RUN-ID/data | HIP/cenário/variante | Braço e SHAs API/Flutter | Executor/versão | Fixture/seed/hash | Estado e motivo | EV-IDs |
|---|---|---|---|---|---|---|
| A preencher | — | — | — | — | — | — |

| EV-ID/RUN-ID | Tipo | Arquivo/URI e acesso | SHA-256 | Ferramenta/versão/unidade/janela | Sanitização/retenção |
|---|---|---|---|---|---|
| A preencher | amostra/trace/SQL/log/snapshot/imagem/áudio | — | — | — | — |

Evidência grande/privada pode ficar fora do Git com referência estável e controle de acesso; não guardar tokens/OTP/contatos/localização privada. Ausência de arquivo, permissão de acesso insuficiente ou evidência não verificável deve ser declarada. Evitar links que só funcionam no diretório temporário removido sem arquivamento sanitizado.

### Resultados especializados quando aplicáveis

**REID:** manifesto/partição/versão dos rótulos e evidência de identidade; indivíduos/fotos/sessões/pares por estrato; TP/FP/TN/FN, precision/recall/F1/FPR/FNR e IC por identidade/sessão; recall de recuperação vs sugestões elegíveis; abstenções/cobertura/restrições; Recall@k e falsa sugestão por consulta open set/tamanho da galeria. Registrar limiar/modelo/política/shadow, curva/tabela de calibração e holdout. Similaridade não equivale a probabilidade ou identificação confirmada. Denominador zero = não estimável.

**ACC:** ID/tela/controle/estado; widget/símbolo no SHA; ferramenta/versão, leitor/OS/plataforma, viewport/DPR/fonte/flags de movimento; critério/valor esperado/observado; EV de screenshot/árvore/áudio/scanner; tarefa concluída/bloqueada, severidade e exceção justificada. Aprovado automático não substitui inspeção assistiva real.

**BR:** valores absolutos A/B e diferenças com IC pareado para latência/recursos/SQL/throughput/integridade. Marcar contagens de mecanismo específicas de B e testes inaplicáveis a A. Não confundir erro preexistente com regressão, nem dependências distintas com efeito exclusivo da refatoração.

## Contrato e integridade

| Oráculo definido antes da rodada | Inicial | Esperado | Observado | Diff/evidência sanitizada | Resultado |
|---|---|---|---|---|---|
| A preencher | — | — | — | — | — |

- JSON/status/mensagem/ordenação/cursor/campos desconhecidos:
- Writes DB/arquivos/ledger/recompensas/eventos após commit:
- Isolamento/autorização e procura por canários de segredo:
- UI/textos/navegação/animações/qualidade visual:
- SMTP/FCM: tentativa, aceite, recepção, exibição com evidência separada:

## Mutação de código quando aplicável

| Mutante/sub-ID | SHA/cópia/diff | Testes | Baseline passou/linha exercitada? | Classificação e causa | Evidência |
|---|---|---|---|---|---|
| A preencher | — | — | — | — | — |

- Mortos/sobreviventes cobertos/não cobertos/equivalentes/invalidade/inconclusivos:
- Score, denominador, cobertura e duração:
- Revisão dos sobreviventes e lacunas:

## Problemas identificados e correções futuras

Preencher somente quando existir evidência; documentação de hipótese não significa bug confirmado nem autorização de implementação.

| PROB-ID | Descrição/impacto/severidade | HIP/cenários/RUN/EV | Código e SHA afetados | Reprodução e esperado/observado | Preexistente/regressão/incerto | Estado/responsável |
|---|---|---|---|---|---|---|
| A preencher | — | — | — | — | — | — |

| FIX-ID/PROB-IDs | Hipótese causal e alteração aplicada | Projeto/arquivos/símbolos | Commit completo/diff da correção | Aprovação/escopo | RUN antes → RUN depois | REV-IDs |
|---|---|---|---|---|---|---|
| A preencher | — | — | — | — | — | — |

| REV-ID/FIX-ID | Cenários/testes repetidos e regressão | SHAs API/Flutter/executor | Condições equivalentes ou desvios | Comparação antes/depois/IC e contrato | RUN/EV | Conclusão |
|---|---|---|---|---|---|---|
| A preencher | — | — | — | — | — | — |

Não sobrescrever baseline ou apagar resultado falho/inconclusivo. Correção revertida/rejeitada recebe estado e commit correspondente. Problema só é encerrado após revalidação dos cenários afetados e dos efeitos funcionais/segurança; ganho de tempo isolado não encerra perda de integridade. Sem equivalência suficiente, comparação fica inconclusiva com motivo.

## Comparação e decisão

- Diferença absoluta/relativa e consistência entre rodadas:
- Hipótese confirmada/refutada, causa e evidência que falta:
- Regressões funcionais/segurança e trade-offs:
- Resultado aprovado/falhou/inconclusivo/bloqueado, com motivo:
- Alteração proposta e testes a repetir:
- Limites: desktop/telefone, fake/IA real, Mailpit/email externo, FCM aceite/exibição:

- Cadeia HIP/RUN/EV/PROB/FIX/REV completa e referências verificáveis:
- Aprovação/rejeição da campanha, responsável/data e próximos IDs pendentes:
- Baseline futura: manter OPT-0 ou aprovar nova versão com SHAs e motivo, preservando histórico:

Nenhum campo vazio significa zero, sucesso ou caso dispensado.
