# Avaliação da especificação de reencontro de gatos

Documento analisado: `app-gatos-especificacao.md`, fornecido pela equipe.
Suas propostas foram avaliadas como material de referência. O foco desta
alteração é o motor de reconhecimento; nenhuma planilha ou JSON anterior foi
usado para identificar gatos, treinar pesos ou calcular acurácia.

## Aproveitado e implementado

| Proposta | Implementação e limite |
|---|---|
| Acrescentar patches DINOv2 | O worker extrai CLS e patches na mesma passagem. A máscara acompanha exatamente o redimensionamento e o preenchimento. Só entram patches com pelo menos 90% de pixels do gato. |
| Verificação local | Até 192 descritores por foto, normalizados e armazenados compactamente. Comparação bidirecional, correspondências mútuas, margem sobre a segunda opção e verificação geométrica. Sem margem, pelagem repetitiva não cria detalhes distintivos artificiais. |
| Refinar apenas pares promissores | A ordenação global precede a comparação fina, limitada por `MATCH_FINE_PAIR_LIMIT` (6 por padrão) para cada candidato. Isso limita comparações entre referências; ainda não é um top-K global de gatos com ANN. |
| Shadow mode | `MATCH_SHADOW_MODE=true` por padrão. As sugestões e a decisão hipotética são registradas, mas não se criam novas notificações automáticas de correspondência. |
| Contrato explicável | `evidence` contém sinal global, sinal denso, geometria, versão, motivos, número de pares examinados e `would_notify`. `probability` permanece nulo e `calibrated=false`. |
| Orientar novas capturas | Sugestões para pelagem sólida/desconhecida incluem rosto, orelhas, peito, patas, cauda e laterais. O objetivo é revelar informação ausente, sem presumir que ela exista. |

O sinal denso pode corroborar uma sugestão de gato com padrão, mas não
confirma identidade. Em pelagem sólida/desconhecida, a sugestão continua
restrita mesmo com geometria compatível. Patches DINOv2 também representam
partes comuns a vários gatos: alta similaridade densa isolada não é prova.

A versão de extração passa para `sam2-dinov2-letterbox-dense-v3`. Fotos
persistidas precisam ser reenfileiradas com `scripts/reindex_recognition.py
--apply`. A coluna `features` já existente comporta os dados; esta mudança
não exige uma migração adicional à `20260908_04`.

## Boas propostas para uma etapa com dados confirmados

- **Calibração e avaliação por segmento:** medir pares de gatos diferentes
  da mesma cor, fotos em dias/ambientes diferentes e consultas cujo gato está
  ausente da galeria. Separar indivíduos e sessões entre treino, calibração e
  avaliação. Calibrar não garante, por si só, transferência para outro domínio.
- **Attention pooling e detector de marcas:** são hipóteses de modelos a
  treinar e comparar. Sem pesos treinados e dados adequados, acrescentar a
  arquitetura não cria capacidade de reconhecer marcas.
- **Fusão visual, tempo e metadados:** manter cada sinal rastreável e sua
  origem registrada. Informação ausente deve ser desconhecida. Proximidade
  não transforma dois gatos visualmente indistinguíveis no mesmo indivíduo.
- **PostGIS e recuperação ANN:** combinar busca indexada por região com
  seleção dos melhores candidatos distintos antes de carregar descritores
  densos. Medir a perda de recall e evitar que várias fotos de um único gato
  ocupem todas as posições. A busca atual ainda não implementa essa etapa
  completa, embora já filtre por região antes da comparação.
- **Feedback de reencontro:** preservar foto/hash, versão, autor da decisão,
  contexto e qualidade da confirmação. O clique de um usuário é evidência a
  revisar; não deve entrar automaticamente como rótulo infalível de treino.
- **Datasets públicos:** avaliar licença, qualidade das identidades e distância
  entre o domínio das imagens públicas e as fotos de celular do aplicativo.

## Corrigido ou descartado como regra geral

| Afirmação da especificação | Avaliação |
|---|---|
| “Comparar manualmente não gera sinal útil” | Incorreto como regra. Anotação humana cuidadosa pode produzir rótulos úteis. Deve admitir “não sei” e registrar a evidência que fundamenta a identidade. |
| “Rajada é positivo garantido” | Não é garantia: pode haver troca de sujeito, vários gatos ou erro na associação. Capturas quase idênticas também não validam reconhecimento entre sessões. |
| “Confirmação do dono é gold label” | Pode ser forte evidência, mas cliques podem conter erro. Confirmação independente do reencontro é mais informativa que aceitar uma sugestão visual. |
| “Corte TNR ou coleira identifica o indivíduo” | São atributos compartilhados ou mutáveis, úteis para compatibilidade, mas não identificadores únicos. Um microchip só serve como identificação quando lido e associado corretamente ao animal; não é extraído de uma foto comum. |
| “XGBoost, não regressão logística” | A escolha precisa de comparação em dados separados. Um modelo simples pode ser mais adequado com poucos exemplos. Complexidade não substitui avaliação. |
| “Trocar average pooling” no motor existente | O código usava CLS, não média aritmética dos patches. CLS é um token processado pelo transformer. Acrescentar sinais locais é útil, mas a troca proposta não descreve literalmente a implementação anterior. |
| “Rosto nunca deve ser principal” | A importância da região depende da pelagem, da vista e do domínio. Resultados com gatos ferais não justificam excluir sinais faciais de gatos sólidos em fotos de celular. |
| “Gatos sólidos não contêm informação suficiente” | Pode ser verdade para determinadas fotos, mas não para todas. Rosto, orelhas, pequenas marcas e outras características podem fornecer informação. A resposta correta diante de informação insuficiente é incerteza. |
| “Aumentar threshold após rejeições” | Pode ocultar um futuro encontro verdadeiro. Primeiro evitar repetição do candidato rejeitado; ajustar critérios só com avaliação do impacto. |
| “Precisão não importa sem volume” | Volume e qualidade são necessários. Volume de rótulos errados ou notificações incorretas pode piorar o produto e o treinamento. |

## Correção dos números geográficos

No estudo citado sobre gatos encontrados vivos com distância disponível
(477 animais), a mediana geral foi **50 m**, com percentis 25 e 75 de 9 e
500 m. Para gatos com acesso ao exterior, a mediana foi 300 m e o percentil
75 foi 1.609 m; para gatos exclusivamente internos, 39 m e 137 m,
respectivamente. A mediana de 315 m apresentada no documento não corresponde
a esses resultados. São estatísticas de uma amostra de recuperações, não uma
distribuição universal de deslocamento de todos os gatos desaparecidos.
[Estudo original](https://pmc.ncbi.nlm.nih.gov/articles/PMC5789300/)

Por isso, não substituí o raio definido para o relato por limites estreitos ou
por decaimento temporal arbitrário. O tempo de upload também não é
necessariamente o tempo da captura ou do desaparecimento.

## Fontes técnicas

- [DINOv2: implementação oficial de CLS e tokens de patch](https://github.com/facebookresearch/dinov2/blob/main/dinov2/models/vision_transformer.py).
- [Pesquisa de reidentificação de gatos ferais](https://arxiv.org/abs/2507.11575): resultados de um cenário específico, não validação do aplicativo.

## Como verificar a alteração

`python -m pytest -q` verifica funcionamento e regressões. Os testes de banco
usam exclusivamente PostgreSQL descartável com as migrações aplicadas.

`scripts/inspect_photo_gallery.py` extrai os novos sinais diretamente de uma
lista de fotos e apresenta uma matriz de comparações sem atribuir identidades.
O relatório desta alteração é `outputs/recognition-dense-photo-check.md`.
Uma melhora de acurácia individual só poderá ser afirmada após avaliação
com identidades confirmadas.
