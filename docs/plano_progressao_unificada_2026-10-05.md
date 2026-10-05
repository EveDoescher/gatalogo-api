# Sistema de marcos: catalogação, missões, conquistas, desafios e rankings

Data-base da auditoria: 05/10/2026. Estado: versão 2 implementada no código da API e do Flutter; concessões continuam sob o rollout existente, sem ativação de ambiente. Este documento substitui as propostas anteriores deste mesmo arquivo, inclusive ranking por XP, oferta de duas missões e desafios sem definição sazonal. Valores e catálogos não acordados estão explicitamente pendentes.

## Decisões aceitas

A atividade principal é descobrir gatos e completar seus registros. Em seguida vêm cuidar do habitat e interagir com a comunidade. Chamaremos o conjunto de objetivos e reconhecimentos de sistema de marcos.

Recompensas: XP, moedas, itens do habitat e itens raros. XP é evolução pessoal, moedas são saldo para mecanismos do habitat e itens personalizam o espaço. Itens raros pertencem à categoria de itens: aparência e interações exclusivas, sem criar outra moeda e sem vantagem obrigatória de XP ou chance de baús. Seu catálogo ainda não foi definido. Conquistas concederão grandes quantidades de XP e moedas; não dependem de itens raros nesta etapa.

| Sistema | Função | Período |
| --- | --- | --- |
| Missões iniciais | Ensinar o fluxo e os mecanismos, com recompensa única | Uma vez por conta |
| Missões diárias | Três objetivos curtos e contextuais | Dia fixo |
| Missões semanais | Três objetivos mais difíceis, que provavelmente exigem várias sessões | Segunda 00:00 até a próxima segunda 00:00 |
| Desafios | Missões longas, difíceis e com grande reconhecimento/prêmios exclusivos | Temporada com início/fim comuns |
| Conquistas | Marcos significativos e permanentes, com medalha própria e grande prêmio | Sem prazo |

Missões, desafios e conquistas podem compartilhar o motor de condições, mas não sua identidade ou apresentação. Uma ação válida pode avançar uma diária, uma semanal, um desafio e uma conquista cujas condições ela atenda. Cada objetivo paga uma vez por instância; o crédito base da ação também não se repete.

## 1. Diagnóstico verificado

| Área | Implementação atual | Consequência |
| --- | --- | --- |
| Ranking semanal | `app/routers/social.py:weekly_leaderboard` ordena `preferences.gamification.xp`, sem intervalo semanal | Compete pelo XP de toda a vida; não reinicia por semana. |
| Desafios/conquistas | `progression.CHALLENGES` define cinco metas permanentes; `finish` também grava essas mesmas metas em `ProductState.achievements` | O mesmo conceito recebe dois nomes. |
| Missões | `progression.projections` gera uma diária e duas semanais, mas sem descrição/unidade/ação de destino | Home inventa uma descrição de fotografia quando a resposta não informa a condição. |
| Cartas com + e patinhas | `GoalCard` gera um quadradinho por unidade da meta entre 2 e 7; + = unidade vazia, ícone = preenchida | Não são cartas, itens colecionáveis ou ações. A unidade pode ser gato, dia, cor ou foto, sem explicação. |
| Mural da Home | `_AchievementShelf` usa `progress.challenges`; escolhe ícones pela posição na lista | O ícone não representa uma conquista específica e muda de significado se a ordem mudar. |
| Missões e conquistas | `JourneyPage` mantém `_history` de `/app/journey`, além do resumo compartilhado | Após uma atualização do store, a lista própria pode continuar antiga. |
| Cache/XP | `CatStore.gamification` alterna entre `ProgressService` e `GamificationService`, com caches diferentes | Fontes duplicadas favorecem estados divergentes. |
| Resultado da ação | `ProgressService.fetchResult` atualiza o histórico, sem aplicar o `summary` retornado | Consultar o resultado não garante que as outras telas recebam a mesma atualização. |
| Ranking no cliente | `FriendsPage` guarda `_leaderboard` e recarrega em notificações ou atualização manual | Não compartilha revisão com o resumo e pode permanecer desatualizado; falhas são silenciosas. |
| Nível/sequência | Ranking usa `calculate_level`; resumo usa `level_for` com nível histórico preservado; resumo expõe maior sequência como `streak`, perfil lê sequência atual | Termos e regras diferentes para o mesmo indicador. |
| Liberação local | `get_settings()` retornou `progression_rewards_enabled=True` e `progression_rollout_percent=0` nesta auditoria | `grant` marca novos eventos inelegíveis; `projections` só considera eventos elegíveis. Configuração de produção não foi verificada. |
| Catálogo legado | `product.ACHIEVEMENTS` tem várias definições antigas; catálogo ativo tem cinco | Contagem, histórico e oferta ativa precisam distinguir conquistas preservadas e atuais. |

Há bases úteis que devem ser preservadas: eventos persistidos, chave idempotente por pessoa, transação com lock, hash para duplicatas, carteira do habitat, cache por conta e notificações após commit. A reforma deve unificar essas bases, sem reiniciar os dados.

## 2. Gato, registro, complemento e identidade

Um gato representa um indivíduo e tem uma ficha única no catálogo da pessoa. Um registro representa um encontro: foto, data, local quando disponível e observações. Uma ficha reúne os registros associados. Cinco encontros com o mesmo gato não são cinco descobertas.

| Resultado | Significado | Efeito nos marcos |
| --- | --- | --- |
| Nova descoberta | Indivíduo diferente confirmado | Conta uma vez para gatos descobertos; pode avançar outros objetivos |
| Complemento útil | Acrescenta detalhes à ficha ou ajuda no reconhecimento | Avança objetivos de enriquecimento conforme a condição |
| Reencontro | Outra ocasião na história do mesmo gato, mesmo sem novos detalhes | Enriquece o histórico; não gera automaticamente prêmio de complemento |
| Repetição | Reenvio da mesma foto, sem nova contribuição | Sem novo prêmio/progresso |
| Identificação pendente | Foto salva e analisada, mas ainda sem decisão de identidade | Não conta como gato novo nem recebe prêmio de descoberta até confirmar |

Contribuição legítima não é uma lista obrigatória de ângulos. Outra vista, uma foto mais nítida, uma marca antes escondida ou detalhe do rosto podem acrescentar informação. Ângulos diferentes ajudam no reconhecimento e, portanto, são contribuições. IA auxilia a avaliação, mas semelhança visual não deve ser usada isoladamente para rejeitar uma foto legítima. Informações pessoais, nome e localização exata não são obrigatórios para completar metas.

Fluxo acordado: fotografar → analisar → comparar com possíveis conhecidos → confirmar destino. Mostrar foto nova e fotos dos candidatos, não uma lista apenas de nomes. Escolhas: “É este gato”, “É outro gato” e “Ainda não sei”. Permitir procurar manualmente e corrigir posteriormente sem perder fotos/observações. A IA sugere; não confirma identidade automaticamente. Associação dentro do catálogo pessoal não equivale a identidade global entre contas ou confirmação de um desaparecido.

No código atual a primeira escolha ocorre antes das sugestões visuais; a busca de candidatos é feita depois no Resultado, dentro da própria conta. Isso será reformulado. Confirmar novidade deve ocorrer antes de conceder prêmio de descoberta, evitando antecipar um prêmio para depois descontar numa decisão normal do fluxo.

## 3. Missões iniciais preparadas para tutorial futuro

Implementar primeiro os objetivos e seus estados; não implementar agora o tutorial com indicações de clique, destaques ou sobreposição. Objetivo e orientação são camadas separadas. Tutorial futuro referenciará IDs estáveis de missões e pontos estáveis de interface; não decide se a missão foi cumprida.

Cada missão terá ID/versionamento, pré-requisitos, condição mensurável, destino para Começar/Continuar, progresso persistido por conta e recibo da recompensa única. Não exigir repetição se a ação já tiver sido validamente cumprida antes da etapa. Migração precisa reconhecer histórico sem pagar duas vezes.

Sequência discutida: primeiro registro e confirmação de identidade; complemento útil; revisão de características da IA; primeiro cuidado no habitat; conhecer a comunidade, com publicação opcional. Detalhar critérios e prêmios antes de ativar o catálogo. Concluir objetivos de atividade exige evidência da atividade; visitar uma tela não substitui catalogar, revisar ou cuidar. Eventual etapa puramente explicativa do futuro tutorial deve ficar separada dos objetivos que concedem crédito por ações reais.

## 4. Diárias e Jornada de hoje

Três missões disponíveis ao mesmo tempo. A sequência na Home organiza apenas o destaque; não bloqueia a segunda ou terceira tarefa. Concluir a terceira primeiro conta normalmente.

Prioridade da oferta: um objetivo de catálogo (descobrir ou complementar), um de cuidado válido do habitat e um variável de catálogo/comunidade conforme oportunidades da conta. Não obrigar a encontrar um gato novo todo dia. Ofertas contextuais são escolhidas pelo servidor e estáveis no período; assegurar alternativas realizáveis para contas sem gato no habitat ou sem ficha para complementar.

Jornada de hoje destaca a primeira pendente na ordem definida. Ao concluir, mostrar confirmação e recompensa recebida, depois destacar a próxima pendente. Ao tocar, abrir uma tela com as três diárias, inclusive concluídas e seus recibos. Ao completar todas, mostrar “Jornada de hoje concluída”.

Contagem regressiva visível antes e depois da conclusão: “Novas missões em HH:MM:SS”. Período/reset vem da API, não de um prazo inventado ao abrir a tela. Ao chegar ao reset, carregar as novas instâncias. Não exibir missões antigas como novas ou inventar zeragem quando offline; informar atualização pendente.

## 5. Semanais

Três objetivos mais difíceis, com maior peso e recompensas que diárias. Devem favorecer atividade em algumas sessões, sem obrigar presença todos os dias. Principal foco em gatos diferentes confirmados e complementos úteis; cuidado/comunidade vêm depois.

Semana fixa de segunda a domingo: reset na segunda 00:00. Não são sete dias contados do primeiro login. Quem entra sexta participa do mesmo período e terá até a virada para segunda. Mostrar prazo e contagem regressiva. Não cumpridas expiram; ganhos concedidos permanecem.

Uma foto ou outra ação válida pode contribuir simultaneamente para diária, semanal e desafio. Isso é comportamento esperado, não um crédito duplicado: cada objetivo recebe seu próprio avanço e seu bônus só paga uma vez.

A versão 2 usa America/Sao_Paulo como calendário comum do jogo; a API fornece o horário de referência e as datas de renovação com offset explícito. Não confiar no relógio do aparelho. Mudança de fuso não pode reiniciar uma instância para ganhar novamente.

## 6. Desafios sazonais

Desafios são missões de longo prazo e alta dificuldade, com etapas claras, reconhecimento alto e prêmios exclusivos. São sazonais: uma temporada tem começo e fim comuns, com desafios daquele período. Não resetam junto das diárias ou semanais.

No encerramento, desafios incompletos expiram; os concluídos ficam no histórico e continuam contando no ranking permanente. Nenhuma temporada apaga gatos, registros, XP, moedas, medalhas ou itens ganhos.

Tema, duração, catálogo de etapas e itens raros não serão aprofundados agora. Três meses foi uma sugestão, não uma duração aprovada. Não oferecer item raro ou outro prêmio exclusivo sem definir e implementar o que ele efetivamente faz. Nenhuma decisão de passe pago foi tomada.

## 7. Conquistas e medalhas

Conquistas são marcos permanentes e significativos, com condição explícita, medalha própria, data de obtenção, recompensa única de bastante XP/moedas e dificuldade proporcional ao prêmio. Não transformar toda pequena ação em medalha.

Famílias discutidas: quantidades relevantes de gatos diferentes; fichas enriquecidas com contribuições úteis; histórico consistente de cuidados; contribuição comprovada à comunidade, como pistas reconhecidas por tutores. A primeira descoberta pode ser um marco de boas-vindas; catálogo, metas e valores ainda serão definidos. Não vincular conquistas a itens raros nesta etapa.

Suas conquistas na Home mostra conquistas reais. Não reutilizar a lista de desafios, escolher ícones por índice ou confundir medalha em andamento com obtida. Mural próprio, separado da área de missões. Conquistas históricas preservadas não podem aparecer como desfeitas após excluir uma foto; eventuais correções e novos objetivos precisam de política explícita.

No perfil, pessoa escolhe medalhas obtidas para destacar. Respeitar audiência/privacidade; não publicar automaticamente no feed. Quem toca vê medalha, nome, marco alcançado e data. Conquistas não obtidas ficam no acompanhamento pessoal.

Experiência acordada:

- Antes: progressão legível, unidade, quanto falta e prêmio. Exemplo ilustrativo: “17 de 20 gatos diferentes — faltam 3”; número não define uma meta de catálogo já aprovada.
- Ao completar: revelação chamativa da medalha, brilho/partículas, som e apresentação destacada de XP/moedas realmente recebidos. Intensidade pode acompanhar importância do marco.
- Ao abrir a medalha: apresentação especial com medalha grande, moldura elaborada, animação, significado e data, para transmitir orgulho de tê-la conquistado. Permitir rever e compartilhar voluntariamente.
- Reabrir não paga de novo. Celebração inicial usa ID do evento para não reaparecer em cada navegação; apresentação de troféu pode ser revista sem simular outra concessão.
- Som e movimento respeitam preferências, redução de movimento e acessibilidade. Texto/composição continuam transmitindo o prêmio e a importância mesmo sem efeitos.

## 8. Três rankings, sem XP como critério

| Ranking | Critério aprovado | Período |
| --- | --- | --- |
| Gatos descobertos/catalogados | Indivíduos diferentes confirmados; reencontros/fotos repetidas não aumentam a contagem | Semanal e geral |
| Missões concluídas | Pontos por diárias e semanais concluídas; semanais valem mais; iniciais fora da competição | Semanal |
| Conquistas e desafios | Pontos por marcos obtidos proporcionais à dificuldade; desafios sazonais concluídos continuam contando | Permanente |

XP passa a servir exclusivamente à evolução pessoal, moedas à economia e pontos à ordenação dos placares. Pontos não são moeda ou nova recompensa gastável. Não derivar esses rankings de saldo, XP acumulado, curtidas ou quantidade bruta de fotos.

Pesos exatos de missões, conquistas e desafios ainda não foram definidos. Devem ser públicos, versionados e mostrados no detalhe, sem critério oculto. Uma grande conquista vale mais que uma de boas-vindas. Permanente significa não reiniciar pontos de desafios concluídos quando a temporada termina.

API deve informar período, critério, revisão do placar e contribuição detalhada da pessoa. Definir desempates, tratamento de correções/duplicatas, retrospectiva de legado e instante de atribuição antes de ativar os placares. Recomendação técnica: contar conclusão/descoberta confirmada no servidor, não data manipulável do aparelho. O placar geral de gatos conta identidades confirmadas únicas, não a soma ingênua de semanas se a identidade foi corrigida.

Escopo atual de amigos pode ser preservado na primeira entrega; ranking global não foi aprovado. Nível pode ser mostrado como informação de perfil, sem ordenar o placar.

## 9. Contrato e sincronização propostos

Um catálogo versionado na API, com definições separadas de instâncias de missões, temporadas/desafios, medalhas obtidas e recibos. Reaproveitar eventos idempotentes e carteira; migrar dados preservados. Separar validação da foto, identidade, revisão de atributos e estado econômico. Rollout desligado não deve confundir atividade legítima com atividade inválida; migração decide quais dados podem avançar futuros marcos sem repagar ganhos antigos.

Resumo único tipado por conta: versão de regra, revisão, data do snapshot, estado das recompensas, calendário/períodos, XP/nível, carteira, iniciais, diárias, semanais, desafios sazonais, conquistas e contribuições para cada ranking. Objetivo contém ID/instância, tipo/família, condição/unidade, valor/meta, estado, começo/fim quando aplicável, arte estável, destino e recibo de prêmio. Não deduzir tipo pelo prefixo do ID.

Respostas de ações confirmadas entregam evento, mudanças dos objetivos e snapshot atualizado. Evento, crédito e conclusão precisam ser consistentes na transação. Resultado histórico não pode sobrescrever resumo mais novo; comparar revisão ou consultar resumo atual. Desacoplar revision do placar da individual, porque amigos também mudam o ranking.

No Flutter, um store compartilhado tipado e selectors por tela substituem caches e listas privadas concorrentes. Cache é último estado confirmado por conta, período e revisão, nunca autoridade para somar ganhos. Preservar offline com aviso e idade; erro não vira zero progresso. Logout/troca de conta impede retorno de requisições antigas.

Atualizar após registro/identidade/complemento/revisão/cuidado/publicação validada, sincronização offline, reconexão, retorno ao app, viradas de períodos e notificação após commit. Reagrupar atualizações repetidas. WebSocket solicita nova projeção, não incrementa valores localmente.

## 10. Apresentação e componentes

Missões ficam separadas em Iniciais, Diárias, Semanais e Desafios. Conquistas têm tópico/mural próprio, embora usem o mesmo motor de condições. Remover cartas genéricas cinzas com + e amarelas com patinhas: elas não explicam a unidade. Mostrar “2 de 3 dias”, “3 de 5 gatos diferentes”, nomes de características ou checklist de etapas com verbo.

Componentes reutilizáveis: cartão de missão, medidor com unidade/meta, prazo regressivo, resumo/recibo de recompensa, medalha, apresentação de conquista, cartão/etapa de desafio, estado de sincronização e linha de ranking específica ao critério. IDs de medalha resolvem arte estável; sem ícones escolhidos pela posição.

Manter direção de arte cozy, assets coerentes com função, papel creme, tinta marrom, verde sálvia e coral. Usar composição mais exuberante nos grandes reconhecimentos. Cor/efeito não são a única informação. Em 320 px, tablet e texto ampliado, descrição/meta/prêmio/prazo permanecem legíveis. Atualizar galeria ao reformular telas, com cenários coerentes derivados da mesma fonte de teste.

## 11. Etapas futuras de implementação

1. Definir catálogos iniciais, diários, semanais e conquistas; metas, valores e pesos públicos. Reservar desafios sazonais sem inventar temporada/prêmios não definidos.
2. Reformular ficha/registro/identidade e complemento útil, com comparação antes do crédito de descoberta. Testar reencontro, contribuição, repetição e identificação pendente.
3. Consolidar motor, instâncias, contrato/revisões, calendário e migração de legado. Auditar flags por ambiente; não ativar 100% ou pagar retroativo isoladamente.
4. Implementar iniciais preparadas para tutorial, diárias/semanais e conquistas com concessão idempotente. Implementar produtores de eventos faltantes antes de ofertar respectivas metas.
5. Implementar os três placares com projeções e intervalos corretos, sem reutilizar ranking por XP. Sem N+1 por participante; filtros de amizade/autorização na API.
6. Unificar store e gatilhos de sincronização; reformular Home, área de missões, mural, perfil e rankings com componentes compartilhados e celebrações confirmadas.
7. Verificar critérios abaixo, atualizar galeria e liberar mecanismos no ambiente autorizado. Tutorial guiado e aprofundamento de temporadas ficam para etapas posteriores.

## 12. Critérios de aceite

- Mesmo gato em cinco encontros = um indivíduo e cinco registros; novo ângulo útil pode enriquecer ficha e marcos sem aumentar ranking de gatos.
- Identificação pendente salva e analisa, sem prêmio/contagem de descoberta até confirmação; comparação e correção preservam dados.
- Reenvio de foto/ação, reconexão e requisições simultâneas não repagam nem avançam indevidamente. Avaliação de complemento distingue repetição de contribuição legítima.
- Uma ação válida avança simultaneamente diária, semanal, desafio e conquista pertinentes; cada bônus paga uma vez.
- Iniciais persistem e reconhecem atividades anteriores válidas sem exigir repetição nem duplicar concessão. Tutorial futuro não precisa redefinir objetivos.
- Três diárias acessíveis; Home destaca primeira pendente, pula as já feitas, abre as três e termina em Jornada concluída com contagem regressiva.
- Semanal é segunda a domingo para todos, não sete dias após login. Virada troca instâncias, preserva prêmios e histórico; offline informa atualização pendente.
- Ranking de gatos ignora volume de fotos; missões usa pesos explícitos e exclui iniciais; conquistas/desafios é permanente e preserva conclusão sazonal.
- XP/moedas antigos não colocam pessoa no topo por si só; pontos não são gastáveis. Correção de identidade não cria novos indivíduos fictícios ou pontuação duplicada.
- Conquista obtida aparece no mural e na Home, recompensa confirmada, data correta; destaque no perfil é escolhido e respeita audiência. Abrir apresentação não concede prêmio novamente.
- Todos os números concordam ao navegar, reconectar ou receber eventos; cache antigo/resposta atrasada não sobrescreve revisão nova nem mistura contas.
- Migração preserva XP, nível alcançado, moedas, itens e medalhas, sem concessão retroativa automática. Catálogo antigo aposentado é histórico, não oferta ativa falsa.
- Meta, condição, unidade, prêmio, prazo e estado são acessíveis com texto ampliado/leitor de tela. Celebração funciona com som/movimento desligados.

## 13. Pontos ainda pendentes

Metas e catálogo concreto; valores de XP/moedas; pesos dos três placares e desempates; fuso definitivo; critérios/evidências de complemento útil e cuidado; política de correções/legado para pontos; quantidade de medalhas destacadas; seleção contextual para contas sem oportunidades. Duração/tema de temporada, itens raros específicos e tutorial guiado serão aprofundados depois. Nenhum número não acordado deve ser tratado como decisão já aprovada.


## 14. Implementação da versão 2 — 05/10/2026

Catálogo central: `app/services/milestones.py`; concessões e recibos: `app/services/progression.py`. Nenhuma migração estrutural nova é necessária: o ledger existente conserva chaves antigas e os saldos. A primeira leitura mantém as medalhas já adquiridas, inclusive as aposentadas. Fotografias legadas sem evidência válida ficam preservadas, sem inventar validação ou pagar retrospectivamente. Prêmios antigos cujo recibo não existe aparecem como medalhas históricas, sem informar o prêmio atual como recebido no passado.

| Grupo | Meta inicial de implementação | XP / moedas | Pontos |
|---|---|---|---|
| Diária 1 | 1 descoberta ou complemento útil | 20 / 10 | 10 |
| Diária 2 | 1 cuidado real, para quem começa o dia com gato no habitat; caso contrário 1 complemento útil | 15 / 10 ou 20 / 10 | 10 |
| Diária 3 | 1 publicação, comentário ou compartilhamento interno válido | 10 / 5 | 10 |
| Semanal 1 | Registros em 3 dias distintos | 100 / 50 | 30 |
| Semanal 2 | 4 complementos úteis | 120 / 60 | 30 |
| Semanal 3 | 3 gatos diferentes | 150 / 75 | 30 |
| Inicial | Primeiro registro, complemento, revisão, cuidado, comunidade opcional | 25/15, 25/15, 20/10, 25/15, 20/10 | 0 |

Números são parâmetros iniciais de balanceamento da implementação, sujeitos à avaliação. A oferta diária é fixada na primeira leitura do dia; alterações no habitat não trocam a instância. Segunda-feira às 00:00 inicia uma semana nova; o relógio do aparelho não decide períodos. Metas já pagas da versão anterior não reabrem mesmo se o alvo atual for maior.

As 11 conquistas ativas são: primeira descoberta; 5, 10, 20 e 50 gatos distintos; registros em 3 dias; 3 cores confirmadas; 2 e 10 complementos úteis; cuidado em 10 dias; primeira pista reconhecida pelo tutor. XP de 100 a 3000, moedas de 50 a 1200 e pontos de 10 a 200 conforme o marco. Até 5 medalhas obtidas podem ser escolhidas para destaque; perfil público respeita audiência independente. Ranking de gatos oferece semana e total; missões é semanal; marcos é permanente. Empates recebem a mesma posição, com ordem estável por ID, e nunca usam XP como desempate.

Novas capturas começam com identificação pendente. A pessoa compara fotos inteiras, confirma gato novo ou conhecido e, neste último caso, indica outro ângulo, detalhe, nitidez ou só reencontro. Confirmar exige conexão; fotos continuam salvas quando offline. Reencontro guarda histórico, sem recompensa de complemento. Foto repetida não gera crédito. Limites de prêmio base não impedem contribuições úteis de avançarem objetivos. Cuidados são provenientes da interação validada do habitat, nunca de um botão de registrar ação arbitrária.

Flutter usa um único ProgressService por conta, com cache, descarte de resposta atrasada após troca de conta e atualização por eventos. Home, missões, medalhas e desafios leem os mesmos seletores. Ranking consulta os mesmos eventos na API. Missões concluídas têm aviso com XP/moedas; medalhas têm apresentação com animação e som conforme preferências. Abrir a medalha novamente é somente apresentação.

Desafios possuem seção sazonal separada, sem temporada ativa inventada. Tutorial guiado, catálogo de itens raros e primeira temporada continuam deliberadamente adiados. Itens de habitat por nível permanecem na concessão existente. Não há prêmio raro fictício nesta versão.

Rollout: ativado para 100% no .env local em 05/10/2026, com autorização do usuário; concessões habilitadas. O padrão do código permanece em 0% para ambientes sem configuração explícita. Não foi executada implantação nem alteração do ambiente remoto.


## 15. Validação de entrega

- API: 158 testes aprovados, 23 pulados pelo ambiente.
- Flutter: suíte completa com 137 testes aprovados e 4 pulados; depois dos ajustes finais de apresentação, 9 testes de Home/perfil/galeria aprovados novamente.
- Análise estática dos componentes e capturas alterados: sem problemas.
- Galeria permanente regenerada, incluindo missões iniciais, semanais, mural, apresentação de medalha, perfil público com medalha e concessões pausadas; capturas verificadas visualmente e em larguras de 320 e 820 com texto ampliado.
- APK de depuração atualizado. A primeira compilação teve timeout; repetição sem daemon persistente, sem monitoramento de arquivos e com dois trabalhadores terminou com sucesso.
- Configuração local confirmada com recompensas habilitadas e rollout de 100%. Após o usuário iniciar a API e os containers, login e consultas autenticadas de progresso retornaram HTTP 200, regra v2 e recompensas habilitadas. Leituras consecutivas mantiveram XP e saldo, sem duplicar pagamentos. Nenhuma atividade fictícia foi criada na conta de testes. Nenhuma implantação, commit ou push foi executado.
