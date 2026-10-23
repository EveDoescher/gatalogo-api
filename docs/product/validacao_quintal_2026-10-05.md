# Quintal — implementação e validação em 2026-10-05

## Resultado

O nome público passa a ser **Quintal** na navegação, loja, ajuda, missões, perfil, notificações e demais textos do app/API. Identificadores de armazenamento, URLs e nomes técnicos anteriores foram preservados para manter compatibilidade com dados existentes.

As visitas usam planos de rota fixos por encontro, com entrada e saída além da área visível na criação do plano. Mover a câmera não recalcula nem teletransporta um visitante. Há seis faixas de visita, probabilidades para visitas adicionais simultâneas e espécies alternadas: cachorro, coelho, pintinho, cervo, raposa, castor, abelha, papagaio e peixe. As rotinas incluem beber, procurar alimento, explorar, descansar, observar os gatos junto da cerca, polinizar, pousar e nadar. As duas margens recebem visitas; voadores têm altura e ordenação de profundidade próprias para passar acima de objetos baixos.

O entregador também chega e sai de fora da área visível. A chegada conserva o prazo de 22 segundos do encontro da API; assistir à animação não concede recompensas. O baú disponível participa da ordenação do cenário e conserva o contorno e a coleta explícita.

A composição evita bases de pedras, árvores e troncos dentro da água, em corredores dos visitantes ou sobre a área editável. Um teste também cobre a separação entre bases sólidas nas fronteiras dos blocos de cenário. Sobreposições naturais de copas e folhagens permanecem possíveis. A cerca conserva o encaixe do modelo e usa seu centro real para a ordenação de profundidade. A projeção de coordenadas negativas foi corrigida para evitar saltos de uma célula nos movimentos.

Vegetação e pedras compartilham uma paleta aplicada no cenário e na loja. Foram renderizadas **152 miniaturas em 320 px**, a partir dos modelos de origem, com quatro orientações. O componente reutilizável mantém cache dos carregamentos e filtragem de imagem de alta qualidade. Os sprites e medidas do mundo continuam independentes das miniaturas.

A ajuda usa o preço atual do petisco e encaminha para missões e conquistas; não apresenta a antiga tabela de recompensas desconectada da progressão.

## Evidências

- 31 testes de unidade/widgets aprovados em `habitat_world_test`, `habitat_game_test`, `habitat_interactions_test`, `habitat_page_test` e `habitat_service_test`. Cobrem rotas, simultaneidade, continuidade, câmera, expansão, seleção, caminhos, cuidados, reservas de móveis, persistência e tentativas idempotentes.
- 44 testes da API aprovados em `test_habitat`, `test_habitat_encounters` e `test_milestones_v2`.
- Dois testes de captura aprovados: matriz do renderizador e sequência de visitas.
- Matriz com **40 capturas**: larguras de 390 e 700 px, expansão inicial e nível 3, zoom 0.8 e 1.6, centro e quatro limites da câmera. Revisão visual realizada em duas rodadas; a segunda confirma o reparo do encaixe das cercas.
- **360 quadros** da sequência real do renderizador, com intervalo de 0.7 s, cobrindo aproximadamente 252 s. A galeria de movimento permite revisar os atlases reais das nove espécies visitantes e do gato, em quatro direções. A sequência é exibida em 4× para facilitar a inspeção.
- Análise estática dos arquivos alterados sem erros; os dois avisos de estilo encontrados em `habitat_world.dart` foram corrigidos e a análise desse arquivo passou sem apontamentos.
- Galeria principal recapturada. O capturador pré-carrega as miniaturas da loja antes de montar seus cartões; uma captura isolada da loja passou e foi conferida visualmente. O fixture de captura substitui os canais de áudio nativos indisponíveis no teste Windows.

## Artefatos locais

- `build/galeria-visual/galeria.html`: galeria permanente do app.
- `build/galeria-visual/quintal-auditoria/index.html`: matriz visual, com filtro e PNGs completos.
- `build/galeria-visual/movimentos.html`: sequência do cenário e atlases animados.
- `build/validation/quintal-final-flows.log`, `quintal-final-visual-motion.log`, `quintal-gallery-assets-ready.log`: resultados do app.

As capturas usam fixtures explícitas e não alteram o progresso da conta de testes. Essa validação cobre as composições registradas e as regras de geometria; não certifica toda disposição possível de móveis nem desempenho em um aparelho físico. Os avisos do detector TFLite no ambiente Windows de captura não são evidência de validação da câmera: esta rodada se concentra no Quintal.

O APK Android de depuração foi compilado com sucesso (`assembleDebug`, 6m22s), usando a configuração local da API. As deprecações do Gradle não impediram a compilação.
