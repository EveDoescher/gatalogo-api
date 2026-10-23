# Validacao funcional dos marcos - 05/10/2026

## Ambiente e verificacao autenticada

API: `http://192.168.1.2:8000`. Login e consultas autenticadas retornaram HTTP 200; regra v2, recompensas habilitadas. Nao foram criadas atividades ficticias nem alterados saldos na conta de testes.

| Verificacao | Resultado |
|---|---|
| Progresso e endpoint de gamificacao | XP, saldo, nivel, objetivos, medalhas e pontuacoes iguais |
| Mural e conquistas da projecao | Listas iguais |
| Rankings de gatos semanal/total, missoes e marcos | Pontuacao da conta igual a projecao em todos os filtros |
| Periodos | Sao Paulo; diarias a meia-noite; semana de segunda a segunda |
| Reabrir/consultar | XP, saldo e revisao mantidos; nenhuma concessao repetida |
| Temporada ainda nao definida | Nenhum desafio ou premio exclusivo ficticio |

## Exercicios de comportamento

No PostgreSQL local, uma conta temporaria foi exercitada em transacao desfeita ao final. A ausencia da conta e dos eventos depois do rollback foi conferida.

- Foto com identidade pendente nao pagou nem contou descoberta.
- Confirmacao de gato novo avancou diaria, semanal, inicial e conquista; primeiro evento pagou 195 XP e 100 moedas, incluindo bonus.
- Repetir a confirmacao manteve XP, saldo e quantidade de eventos.
- Foto de outro angulo para gato conhecido avancou complemento sem incrementar gatos diferentes.
- Reencontro nao concedeu premio de complemento.

Testes selecionados da API: 63 casos distintos aprovados (61 da selecao inicial e dois testes de integracao novos, incluidos nos 14 casos aprovados ao repetir o arquivo de marcos). Os testes novos usam os endpoints de cuidado e publicacao: concluem as missoes aplicaveis, pagam uma vez e rejeitam destino invalido sem avancar progresso. Cobertura adicional inclui identidade corrigida, hash repetido, revisao, pista reconhecida, pausas, limites, migracao, medalhas destacadas, recibos historicos e virada de periodo.

Flutter: 11 testes aprovados (10 testes selecionados e uma validacao completa de capturas da galeria); incluem dados reais da projecao na Home, larguras e texto ampliado, troca de conta durante resposta atrasada, cache offline e tela de amigos.

## Historico da conta e limites da conclusao

A conta de testes tem 30 registros remotos, dos quais 9 foram excluidos. Os 21 registros ativos pertencem ao sistema antigo, sem estado de identidade explicito e sem evento de atividade validada. Por isso, o novo contador de gatos confirmados esta em zero. As 12 medalhas antigas, 2759 XP, nivel 6 e 9 moedas foram preservados. O ranking permanente de marcos considera 115 pontos. O mural tem 18 medalhas ao incluir as historicas; 11 sao do catalogo ativo.

Esta preservacao nao equivale a revalidar todos os gatos antigos. Nenhuma descoberta, complemento ou cuidado retroativo foi inventado. Caso a experiencia desejada inclua esses registros antigos nos novos contadores, sera necessaria uma regra explicita de validacao/migracao; a ausencia desse credito nao foi encoberta nos resultados.

Os exercicios no PostgreSQL verificam os servicos e a persistencia, sem nova chamada ao detector visual. Os testes do Flutter usam fixtures controladas e nao substituem um percurso manual completo em aparelho, particularmente camera, inferencia real, notificacao push e percepcao de animacao/som.

A galeria permanente foi regenerada e o teste de capturas terminou com sucesso. Home com progresso, missoes semanais e apresentacao da medalha foram inspecionadas visualmente. As capturas representam cenarios controlados de validacao, nao o saldo da conta real.
