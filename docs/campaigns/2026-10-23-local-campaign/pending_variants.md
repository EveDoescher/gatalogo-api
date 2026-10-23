# Pendências preservadas por variante

O plano de 95 cenários continua válido. Uma suíte verde aprova somente os casos e condições executados, nunca todas as variantes da linha. [Matriz JSON](matrix.json) conserva o escopo de cada cenário e liga as novas tentativas; [rastreabilidade](traceability.json) identifica cada caso, executor e fonte.

| Próxima bateria local segura | Variantes ainda necessárias |
|---|---|
| Contratos e autenticação | Priorizar revisão da falha Apple antes de mudanças; acrescentar controles de identidade externa legítima com verificação simulada confiável, vínculo/subject/campos conflitantes e efeitos em sessões. A suíte atual comprova rejeições ausentes, não a correção. |
| Notificações e conversa | Integração real entre assinaturas WS/push nas duas ordens para o mesmo ID, paginação/read/refresh e troca de conta; anexos/texto com respostas de detalhes e histórico atrasadas alternadamente. Races de store e transporte real foram executados em experimentos distintos. |
| Catálogo e fotografia | 0/40/501 dirty e todas as variantes de cache/online/offline, fechamento/reabertura após captura/edição, interrupção durante upload e respostas antes/depois do commit com barreiras distintas. O novo catálogo só mede leitura local; o pipeline HTTP não é reinício de app. |
| Recursos e SQL | Conversas com 0/100/1.000 mensagens por conversa; planos de feed/inbox/Quintal e demais consultas críticas; ciclos de escrita/upload/substituição/exclusão e repouso. RSC-02 atual cobre leituras e erros. |
| Resiliência de serviços | Falha controlada de DB que produza erro/timeout, retomada e integridade além de pausa curta; cache frio/quente de segmentação e processo, serviços/provedor indisponíveis, limites CPU/RAM e concorrência fixos. Corrupção sem recuperação foi caracterizada, não resolvida. |
| Evidência estática de dependências | Revisar os 50 grupos de avisos já consultados, versões/withdrawn/precondições e chamadas efetivamente alcançáveis; confirmar instalação/deployment e dependências nativas. Correspondência de versão não é prova de exploração. |
| Acessibilidade automatizada | Ampliar de login/controle isolado para todos os fluxos/estados e combinar foco, semântica, escala, hit areas, redução de movimento, safe areas, teclado e orientação suportada. Capturas visuais amplas não substituem asserções de acessibilidade. |
| Comparação e confirmação | Completar blocos pareados com host qualificado, amostragem pré-especificada e todas as cargas escolhidas. `/health` teve aquecimento instável e não demonstra ganho de refatoração. B só se torna baseline OPT-0 após registrar a revisão A/B correspondente (BR-05). |

Estas pendências locais não são classificadas como dependentes de aparelho por conveniência. A implementação/execução ainda deve avançar onde houver fixtures e mecanismos seguros; resultados vermelhos devem permanecer visíveis, sem mudar funcionalidades nesta etapa.

Continuam fora desta etapa: patamares prolongados LOAD-01/02/06 e soak, IA paga, aparelho físico, bateria/rádio/temperatura do celular e entrega real Firebase. Reidentificação precisa de identidades/consentimento/splits rotulados confiáveis e galeria controlada; fotos disponíveis no workspace, sozinhas, não estabelecem esses pré-requisitos. TalkBack/VoiceOver e combinações de estado do app real permanecem pendentes. Não há identificação confirmada de um gato por essas provas sintéticas.

Os cinco antigos skips visuais foram efetivamente executados; dois revelaram goldens divergentes. As verificações físicas continuam pendentes. Os históricos de mutação e acessibilidade anteriores permanecem íntegros e nenhum problema aberto foi silenciosamente considerado corrigido.
