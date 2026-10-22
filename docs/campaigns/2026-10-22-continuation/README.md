# Campanha Gatálogo — continuação de 22/10/2026

A rodada ampliou segurança, integridade, resiliência, mutação e acessibilidade sem
alterar `app/` da API ou `lib/` do Flutter. A `main` dos dois repositórios permanece
nos SHAs registrados. Os serviços habituais foram preservados. Os bancos, contas,
storage e Mailpit dos experimentos foram descartáveis; IA/FCM foram simulados.

O planejamento **não está integralmente concluído**: dos 95 cenários, 41 têm execução
parcial, 42 continuam pendentes, um está preparado e 11 dependem de pré-requisitos
externos. Nenhum cenário foi promovido a concluído. Teste de caracterização que
passa ao reproduzir um problema não significa que o produto está seguro.

## Resultados principais

| Bateria | Resultado e escopo |
|---|---|
| API ampliada `extended-03` | 129 passaram; autorização ator×ação, conflitos HTTP, rollback, OTP/SMTP, eventos, referências e regressões selecionadas. |
| API dirigida `targeted-02` | 6 passaram; limites, concorrência 2/10/25 e observações de revogação/SMTP antes do commit. |
| Filas/fanout `queue-01` | 9 passaram; 1/10/50 jobs × 1/2 consumidores com inferência simulada, mais fanout sintético 1/10/50. Não mede capacidade sustentada. |
| Privacidade `privacy-02` | B: 2 passaram e exportação falhou; A: controle passou e mesma exportação falhou. Defeito preexistente. |
| API completa `full-regression-01` | 329 passaram/1 falhou, sem skips; inclui integração real HTTP/Flutter de perda de resposta após commit e reinício. A falha de recompensas foi diagnosticada como configuração de rollout e revalidada separadamente. |
| Rollout explícito | Cenário original passou em 100%; cenário de atividade sem recompensa passou em 0%. Não se apagou o resultado vermelho anterior. |
| Flutter completo `flutter-full-01` | 278 passaram/6 skips, exit0, aproximadamente 500,5s de duração total da suíte; não é latência do app. |
| Resiliência Flutter | Baseline de mutação 65 passaram; oráculo de inbox ampliado 18 passaram; bateria final SQLite/sync/conta/401 com 14 passaram. Há sobreposição entre baterias. |
| Acessibilidade `accessibility-04` | 15 passaram/12 falharam: 12 células do login com achados; hold4, erro/teclado1 e Habitat10 passaram. Sem leitor físico ou certificação global. |
| TCP WebSocket `network-01` | Duas caracterizações reproduziram typing entregue por conexão antiga após revogar/expirar sessão persistida. HTTP401 e novo upgrade403; controles positivos e entrada inválida/outsider também exercitados. |
| Contexto Dart `probe-layout-01` | Quatro probes A/B passaram, mais 11 testes de conversa/inbox. Três tools sem issues no analisador; 18 testes de infraestrutura passaram. |

Os seis skips Flutter correspondem a capturas corrigidas com fontes, animação de
chegada/entrega/saída, previews de produto, cliente live isolado, matriz visual do
renderer e capturas de telas alteradas. A maioria são capturas opcionais; o cliente
live foi executado na bateria API própria, sem transformar esse skip em execução.

## Problemas reais, sem correção no produto

- **Exportação com MissingReport:** usa atributo inexistente `cat_client_id`; contrato
  falha em A e B. Não se atribui esse defeito às refatorações.
- **Arquivo órfão após rollback:** banco e arquivos antigos permaneceram intactos,
  mas um arquivo novo ficou sem referência. Avaliar compensação por referência/idade.
- **SMTP antes do commit:** Mailpit próprio aceitou mensagem e o commit injetado falhou;
  zero OTP persistido. Avaliar outbox/ordenação e idempotência antes de implementar.
- **WebSocket aberto e sessão invalidada:** evento typing chega ao destinatário mesmo
  após HTTP e novas conexões serem negados. Agora também reproduzido em TCP real;
  não cobre todas as ações, expiração JWT no relógio ou tempo máximo de exposição.
- **Acessibilidade do login:** contraste, áreas de toque e corte de texto ampliado.
  Os achados anteriores continuam abertos; nenhuma aparência foi modificada.

O [registro de problemas](issues.json) mantém hipótese → teste → resultado → problema
→ recomendação → correção/revalidação pendente. Commits de correção funcional estão
vazios porque essa etapa não autorizou alterações de produto.

## Mutação e erros dos executores

Histórico consolidado: **34 execuções**, 32 kills, um sobrevivente histórico e um
resultado histórico inconclusivo. Na execução válida mais recente: **29 variantes
mínimas detectadas nas 28 famílias**. Isso não é um escore global de mutação nem
prova de ausência de defeitos; cada família continua parcial.

O sobrevivente MUT-07 do histórico motivou um oráculo de estado intermediário. Na
rodada Flutter, o classificador antigo tratava `failure` incorretamente; a revisão
acrescentada usa os JSONL preservados. MUT-22 permaneceu inconclusivo na revisão
estrita inicial e depois foi morto por uma asserção explícita de isolamento de inbox.
Não se contam erros de compilação, setup, timeout ou skip como kills.

`extended-01` falhou por amizade ausente na fixture; `targeted-01`, por tentar alterar
uma propriedade calculada; ambos foram corrigidos nos executores e reexecutados.
`flutter-resilience-01` teve um skip e corretamente não aprovou o gate de mutação.
O rollout0 explica a falha de recompensas da suíte completa e do primeiro diagnóstico.
Essas tentativas continuam registradas, sem reescrita das evidências originais.

## Dependências, desempenho e limites

A [triagem de dependências](dependency_triage.md) documenta a consulta autorizada ao
OSV: 254 nomes/versões, seis pacotes com correspondência, 72 IDs agrupados em 50
componentes de aliases, sem erros. Aplicabilidade e explorabilidade continuam
pendentes; nenhum exploit, atualização de versão ou correção foi executado.

As contagens SQL e os tempos do piloto anterior continuam exploratórios. Não há
novo ganho confirmado de latência, throughput ou consumo. A amostra do host foi
coletada durante trabalho concorrente, com cerca de 14GiB de RAM utilizada de 16GiB;
não satisfaz condições confirmatórias. O novo probe de organização também não é uma
campanha confirmatória. Os helpers de estacionariedade/IC foram implementados e
testados; ainda faltam integração do executor com orçamento, IO/energia/temperatura,
janela controlada e dimensionamento final pré-registrado. P99 continua sem conclusão.

TalkBack/VoiceOver, aparelho/energia, entrega FCM real e reidentificação com identidades
rotuladas/consentidas permanecem pendentes. Nenhuma sugestão de correspondência foi
tratada como identidade confirmada. Não houve IA paga nem carga prolongada.

## Próximas baterias

1. Completar variantes locais de SMTP (recuperação de conta, atraso/aceitação ambígua),
   logs com canários e privacidade/paginação após alteração de preferência.
2. Exercitar cliente/API cruzados de BR-04 com roteiro comum e fontes congeladas;
   BR-04 continua pendente, pois probes de componente não substituem esse experimento.
3. Ampliar resiliência TCP, falhas de DB/storage, reconexão e consumidores lentos com
   barreiras e limites curtos; não inferir capacidade a partir do fanout sintético.
4. Preparar janela estável e executor confirmatório com orçamento, mantendo A/B
   comparáveis. Não iniciar patamares prolongados sem autorização específica.
5. Avaliar recomendações dos problemas antes de qualquer correção/otimização e
   depois revalidar antes/depois com novos commits e as mesmas fixtures.

A [matriz](matrix.md) preserva todos os cenários e seus limites. Variantes locais
pendentes não são descritas como bloqueadas por falta de aparelho. Nenhum teste
restante foi descartado por já haver regressões verdes.

## Evidências e reprodução

- [Resultados por tentativa](results.json), [observações](observations.json),
  [rastreabilidade](traceability.json) e [histórico de mutação](mutation_history.json).
- [Registry de mutação](mutation_registry.json), [contagens](counts.json) e
  [protocolo confirmatório](confirmatory_protocol.json).
- [Fontes e commits dos executores](source_manifest.json),
  [preservação de produto/main/serviços](continuation_environment_check.json) e
  [validação da infraestrutura](validation.json).
- [Índice SHA-256](evidence_index.json) e [recibos dos arquivos privados](archive_receipts.json).
- [Comandos dos executores](../../../scripts/campaign/CONTINUATION.md).

Os snapshots por tentativa são a referência exata do executor usado, inclusive quando
antecedem o commit consolidado. Logs/JUnit/SQLite/tars podem conter tokens descartáveis
e permanecem privados em `.tmp`. O arquivo final verificado contém 1.457 membros,
SHA-256 `e96eac6b05009fdeeca45de0f59426af7155209290089ecbc26bc4f37043675e`,
sem remover originais. Relatórios do piloto e da bateria crítica anterior permanecem
inalterados; estas contagens não devem ser somadas como testes únicos.
