# Continuação — recuperação, históricos e acessibilidade

Este incremento preserva o [relatório local anterior](../2026-10-23-local-campaign/README.md), os testes vermelhos e todas as tentativas de executor. Não modifica produto, dependências, interface, regras de negócio nem `main`. A falha Apple e a incompatibilidade do envelope de chat continuam abertas no [registro anterior](../2026-10-23-local-campaign/findings.md).

| Bateria | Resultado e limites |
|---|---|
| `identity-controls-01` | 14 passaram, sete em cada API A/B: verificador Google rejeitando, emissor errado, e-mail não verificado, indisponibilidade, configuração ausente e caminho positivo com conta ativa/desativada. Verificador externo simulado; não valida Google real nem corrige Apple. |
| `offline-flow-02` | Passou em cinco processos Flutter: captura/edição offline de dois registros, reabertura, sync real, leitura pela outra conta e reabertura final. SQLite e arquivos reais próprios; análise/GPS desativados. Um upload HTTP explícito posterior comprovou proprietário 200 / outra conta 404; não foi apresentado como upload automático de uma análise pendente. |
| `upload-transport-01` | Três passaram: corpo multipart cortado, desconexão antes do commit com barreira de row lock e resposta perdida após commit. Retry preservou hash/conteúdo e uma linha primária; revisões foram medidas, sem presumir exactly-once. |
| `social-history-02` | 18 passaram: 1/20/100 conversas × 0/100/1.000 mensagens × autores repetidos/distintos. Validou não lidas, última mensagem, histórico completo sem duplicação e acesso estrangeiro 403. Até 100.000 mensagens mais um canário por fixture; poucas requisições, sem patamares de carga. |
| `accessibility-flows-02` | 48 células de UI falharam nas diretrizes/limites registrados; um teste do destino privado da evidência passou. São quatro páginas × duas larguras × três escalas × duas preferências de movimento, com duas posições de rolagem: 96 observações. Sem erro de plugin da tentativa inicial. |
| `dependency-static-02` | 50 grupos de avisos já consultados receberam revisão estática de precondições, caminhos e dependências instaladas. Zero novas requisições externas, exploits ou atualizações de pacote; não certifica exploração, ausência de risco ou deployment. |
| `social-order-01/02` | Contrato de invariância de apresentação com timestamps iguais: o teste mantém os mesmos dados lógicos e muda somente a ordem física de inserção. A prova falhou tanto em A quanto em B, além da tentativa B inicial. É comportamento preexistente; não se escolheu um novo desempate para o produto. |

Os [resultados](results.json) registram 156 execuções, incluindo tentativas repetidas e falhas corrigidas do teste; não somá-los como cenários únicos. A [matriz](matrix.md) conserva 95 cenários, agora 65 parciais, 18 pendentes, 11 com pré-requisitos externos e um preparado. Nenhum cenário integral foi aprovado por este incremento. RES-01 e RES-02 avançaram de pendentes para variantes locais executadas; seus limites seguem explícitos.

## Resultados que orientam as próximas decisões

Com 1.000 mensagens sintéticas, a rota de histórico retornou 1.000 itens e cerca de 465 KB, mesmo recebendo `limit=20`. A rota não declara esse parâmetro: é comportamento do contrato atual, não uma paginação implementada que falhou. A lista manteve o crescimento de SELECTs já observado. Os detalhes por célula incluem tamanho da resposta, consultas, tempo exploratório, CPU e RSS; são três requisições por caminho, a primeira de aquecimento. O host foi compartilhado com parte da auditoria de UI; não há conclusão de latência, throughput sustentável ou P99.

Na interrupção antes de commit, a conexão foi encerrada enquanto o handler estava bloqueado em row lock. Ao liberar o lock, o servidor confirmou a atualização. Isso demonstra por que socket perdido não comprova rollback. O retry manteve a mesma chave/hash e não acrescentou uma terceira revisão nos dois casos em que a segunda revisão já havia sido confirmada. O corpo incompleto deixou o estado anterior intacto. A prova verifica banco e bytes retornados, não um inventário completo de arquivos órfãos.

Os testes de empate mostram uma condição importante para futuras otimizações: `ORDER BY created_at` não estabelece desempate entre mensagens com o mesmo horário. Alterar a ordem física dos mesmos registros mudou a ordem retornada e a mensagem escolhida como última, mantendo duas não lidas. A evidência mantém o problema visível; definir um critério secundário exige avaliar compatibilidade antes de alterar a apresentação.

## Acessibilidade: observações da execução final

As regras Android e iOS do SDK foram aplicadas ao mesmo renderizador com plataforma Android simulada; isso não executa o app no iOS. As contagens abaixo são posições de viewport com falha, entre 24 observações por página; não representam bugs independentes nem todos os controles da tela.

| Página | Contraste | Alvo Android | Alvo iOS | Rótulos |
|---|---:|---:|---:|---:|
| Coleção | 24 | 24 | 16 | 24 |
| Detalhes do gato | 24 | 22 | 22 | 14 |
| Perfil | 8 | 24 | 24 | 24 |
| Configurações | 14 | 24 | 24 | 24 |

Exemplos observados: contraste 2,55 em ações sobre rosa; textos auxiliares da coleção em 1,78 e 1,46; controles de cabeçalho com 42/44 px e nós acionáveis sem rótulo segundo o SDK. Limites parcialmente cortados pelo viewport podem parecer menores: revisar o controle também totalmente visível antes de decidir sua geometria. Truncamento de texto foi registrado para análise manual, sem classificar automaticamente toda elipse intencional como defeito.

O reporter Flutter usa `error` para as asserções vermelhas destes testes; as execuções finais não apresentaram erro de compilação ou plugin. Houve oito observações de overflow com escala 2,0: detalhes nas duas larguras (4 px embaixo), perfil em 412 após rolagem (5 px embaixo) e configurações em 320 após rolagem (22 px à direita), repetidas com redução de movimento ligada/desligada. São três padrões de layout. A preferência foi injetada, mas este teste não certifica que todas as animações a respeitam. TalkBack/VoiceOver, tablet, safe areas, teclado e estados adicionais permanecem no plano.

## Revisão estática de dependências

O [registro por grupo](dependency_applicability.json) conserva aliases, referência primária já recebida, descrição, classificação, justificativa, caminhos/hashes e metadados da instalação. Os pacotes continuam sendo Pillow, PyJWT, urllib3, certifi, Mako e hydra-core; as 50 correspondências não foram convertidas em 50 vulnerabilidades confirmadas.

A revisão distingue parsers/decodes potencialmente alcançáveis, operações específicas não encontradas no app e precondições de modelos, templates, proxy, bibliotecas transitivas e trust store. Ausência de chamada direta não prova ausência em dependências nativas. As restrições de formatos do pré-processamento não foram generalizadas para todos os caminhos de mídia. A primeira classificação de três grupos foi refinada e preservada como histórico em [correções dos testes](harness_corrections.json).

## Rastreabilidade e continuidade

- [Resultados](results.json), [observações](observations.json), [rastreabilidade](traceability.json), [snapshots](executor_snapshots.json) e [fontes](source_manifest.json).
- [Problemas abertos](issues.json), [correções dos testes](harness_corrections.json), [matriz](matrix.json) e [pendências](pending_variants.md).
- [Índice SHA-256](evidence_index.json), [recibos privados](archive_receipts.json), [validação](validation.json) e [preservação do ambiente](environment.json).

Mutações não foram executadas neste incremento: permanece o histórico anterior de 29 variantes mínimas mais recentes detectadas em 28 famílias, com sobrevivente/inconclusão históricos preservados. Os novos contratos vermelhos não são baseline verde para mutação. Nenhuma correção de produto foi aplicada; os respectivos commits de correção e revalidações continuam nulos.
