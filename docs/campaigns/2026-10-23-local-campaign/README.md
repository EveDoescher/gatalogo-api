# Campanha local — 23/10/2026

Esta etapa acrescenta evidências ao [relatório anterior](../2026-10-22-continuation/README.md), sem substituí-lo. Não houve alteração de `app/`, `lib/`, regras de negócio, interface ou `main`. As falhas encontradas permanecem abertas para avaliação posterior.

O problema prioritário é a autenticação Apple: nas duas APIs congeladas, quatro variantes de identidade não confiável geraram sessão para uma conta sintética existente. Há também uma incompatibilidade reproduzida entre o evento real de chat e seu consumidor Flutter. Consulte [problemas e recomendações](findings.md) antes de qualquer otimização.

| Bateria / evidência em `attempts/` | Resultado e alcance |
|---|---|
| `delivery-01` | 5 passaram: recuperação por SMTP real próprio, fluxos confirm/grant, uso único, cooldown, isolamento, envio ambíguo e envio lento. |
| `crossed-api-A-02`, `crossed-api-B-02` | 4 passaram: API A/B × cliente A/B, mesma prova externa de serviço, resposta perdida após commit, reinício de processo com o mesmo SQLite e conta distinta. BR-04 agora é parcial executado. |
| `social-volume-02` | 13 passaram: conversas/feed em 1/20/100, autores repetidos/distintos, empates e paginação sem perda/duplicação; revogação de amizade e filtro privado. Revelou crescimento de consultas SQL. |
| `image-02` | 28 passaram: formatos/modos/EXIF, limites e rejeições, cache real com modelos/provedor falsos, JSON e retry. Inclui caracterizações de problemas; não mede acurácia real. |
| `storage-resilience-01/02` | 5 variantes distintas passaram após corrigir dois oráculos: falta de espaço/permissão, limpeza pós-commit, arquivo ausente e pausa/retorno de PostgreSQL próprio. A primeira tentativa conservou suas falhas. |
| `flutter-social-01` | 6 passaram: polling de 30 s, reconexão, eco duplicado nas duas ordens com fixtures planas, histórico tardio e isolamento da caixa de entrada. O contrato real do chat é avaliado separadamente. |
| `mechanisms-01/03` | 16 variantes passaram: galeria por audiência/ator, ciclo de busca/pistas, configuração, recursos e 2/10/25 compras HTTP com intenções distintas disputando saldo de uma compra. |
| `pipeline-01` | 7 passaram: análise HTTP→persistência→metadados→sync, liberação de conexão durante inferência falsa pausada e rejeições. Não inclui reinício de app móvel ou inferência paga. |
| `worker-race-01/03` | 7 variantes distintas passaram: exclusão/substituição durante claim, bytes alterados antes da leitura e encerramento real de processo após claim seguido de recuperação por outro processo. |
| `query-plan-01` | 3 passaram: 40/500/2.000 gatos e 400/5.000/20.000 revisões em schema migrado, `ANALYZE` e planos completos com buffers. Não representa todas as famílias de consultas. |
| `socket-fanout-02` | 3 passaram: 1/10/50 sockets TCP reais, mensagens HTTP persistidas, reconexão parcial e leitor temporariamente atrasado. Não é limite de capacidade nem saturação sustentada. |
| `wire-contract-03` | Caracterização confirmou falha de contrato; a asserção Dart interna continua vermelha. O teste Python passou por reconhecer especificamente a falha esperada, não por aprovar o comportamento. |
| `identity-01` | 8 falharam por comportamento do produto: quatro entradas não confiáveis em cada API A/B receberam HTTP 200, sessão da conta sintética existente e acesso autenticado ao sync. |
| `flutter-notifications-01` | 4 passaram: página pendente com refresh/clear/dispose e recuperação após erro. Não exercita entrega Firebase nem a integração entre suas assinaturas. |
| `flutter-catalog-01` | 3 volumes passaram: 0/40/501 itens visíveis; 1/3/5 leituras SQLite respectivamente, 23 observações por volume. Processo/conexão novos, cache de disco do OS não controlado; sem latência de celular/online. |
| `flutter-visual-01` | 3 passaram / 2 falharam: captura da cena/Quintal/telas executada; comparações antigas de golden divergiram. |
| `flutter-visual-A-01` | As mesmas duas comparações falharam em A; login/home/my_cat gerados em A e B são byte a byte iguais. Os helpers são próprios de cada versão; não é certificação visual integral. Nenhum golden foi atualizado. |
| `health-bounded-01` | 12 células tentadas, 6 estáveis × 1.000 requisições = 6.000 observações. As outras 6 falharam no aquecimento pré-especificado. Integridade das respostas coletadas igual; comparação de ganho não estabelecida. |

As 180 execuções de casos registradas incluem tentativas falhas/repetidas; não são 180 cenários únicos. São 95 cenários planejados: 63 parciais, 20 pendentes, 11 com pré-requisitos externos e 1 preparado. Nenhum foi declarado integralmente concluído. [Matriz](matrix.md), [JSON](matrix.json) e [pendências por variante](pending_variants.md) preservam o alcance original.

## Medição e interpretação

Conversas executaram 7/64/304 SELECTs em 1/20/100 registros, tanto com autores repetidos quanto distintos. Feed com autores distintos executou 5/24/104; com o mesmo autor e estado associado presente, 5/5/5. Cada célula teve três requisições, uma de aquecimento: sustenta a observação estrutural de consultas, não uma estimativa confiável de cauda ou de ganho de latência.

RSC-02 executou 1.200 requisições ASGI em três rodadas de 100 ciclos com quatro leituras/erros por ciclo. RSS foi de 164.204.544 a 163.008.512 bytes; handles de 221 a 208. Esse trecho não apresentou crescimento contínuo observado, mas não certifica ausência de vazamento em uploads, envio de mensagens, worker ou dispositivo.

O protocolo de saúde registrou antes da coleta a ordem ABBA em três blocos, 1.000 requisições por célula estável, até seis janelas de aquecimento, orçamento de coleta de 120 s, piso de RAM e metadados do host. A falta de estabilidade em metade das células impediu a comparação pareada completa. P99 ficou desativado; intervalos individuais condicionais não demonstram independência. Não houve escolha posterior de amostra para favorecer uma versão. A coleta é apenas de `/health` em ASGI, sem SQL, servidor TCP ou inferência, e não substitui a campanha confirmatória do plano anterior.

Mutação permanece com o [histórico publicado](../2026-10-22-continuation/mutation_history.json): 29 variantes mínimas mais recentes detectadas em 28 famílias. Este incremento não executou novos mutantes. O sobrevivente e a inconclusão históricos continuam preservados; correções de oráculos posteriores não apagam esses resultados.

## Preservação e rastreabilidade

- [Resultados por tentativa](results.json), [observações](observations.json), [rastreabilidade por caso](traceability.json), [snapshots dos executores](executor_snapshots.json) e [fontes avaliadas](source_manifest.json).
- [Problemas](issues.json): hipótese → teste → resultado → recomendação; commit de correção e revalidação permanecem nulos, pois nenhuma correção de produto foi autorizada nesta etapa.
- [Índice de evidências](evidence_index.json): caminho relativo privado, tamanho e SHA-256; logs, JUnit, bancos SQLite, eventos e imagens brutos permanecem locais.
- [Validação das fontes](source_verification.json), [ambiente](environment.json), [comparação visual](visual_comparison.json), [validação dos executores](validation.json) e [recibos de arquivo](archive_receipts.json).

As 472 fontes congeladas de produto mantiveram o conteúdo dos quatro SHAs, com apenas tradução CRLF. As duas `main` mantiveram seus SHAs; API usual respondeu 200 e os dois serviços originais permaneceram saudáveis. As alterações do usuário em `Evidencias_Gatalogo` e os artefatos externos à campanha ficaram fora dos commits.

Os snapshots e seus hashes identificam exatamente os executores de cada tentativa, inclusive versões anteriores aos commits consolidados. O commit de publicação dos executores é distinto do commit do produto avaliado. O [roteiro de reprodução](../../../scripts/campaign/CONTINUATION.md) exige nomes novos para não sobrescrever evidências.
