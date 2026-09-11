# Linha de base — Oxford-IIIT Pet v1

Data da execução: 26 de agosto de 2026.

Esta é a linha de base da etapa local **Faster R-CNN + SAM 2 Tiny**, medida
somente nas 48 imagens do split `evaluation`. As 96 imagens de `calibration`
não foram usadas para calcular estes números.

## Configuração medida

| Item | Valor |
| --- | --- |
| Detector | Faster R-CNN MobileNet V3 Large 320 FPN (COCO) |
| Refinamento | SAM 2.1 Hiera Tiny |
| Dispositivo | CPU |
| Confiança mínima do detector | 0,60 |
| Limiar para dois gatos principais | 0,45 |
| Maior lado da imagem de visão | 1280 px |

## Resultado geral

| Métrica | Resultado |
| --- | ---: |
| Fotos avaliadas | 48 |
| Máscaras utilizáveis | 45 (93,75%) |
| Sem detecção | 3 (6,25%) |
| IoU estrito — média | 0,8291 |
| IoU estrito — mediana | 0,8349 |
| IoU estrito — p10 | 0,7555 |
| IoU inclusivo — média | 0,8168 |
| IoU inclusivo — mediana | 0,8243 |
| IoU inclusivo — p10 | 0,7588 |

`IoU estrito` compara a máscara prevista ao núcleo do animal anotado.
`IoU inclusivo` também inclui a região não classificada do trimap nas bordas
do pelo. Nos trimaps do Oxford-IIIT Pet, os valores são: 1=animal, 2=fundo e
3=região não classificada.

## Resultado por raça

| Raça | Segmentadas | Total | IoU estrito médio |
| --- | ---: | ---: | ---: |
| Maine Coon | 4 | 4 | 0,77 |
| Egyptian Mau | 4 | 4 | 0,78 |
| Bengal | 4 | 4 | 0,78 |
| Sphynx | 2 | 4 | 0,80 |
| Birman | 4 | 4 | 0,82 |
| Abyssinian | 4 | 4 | 0,82 |
| Russian Blue | 4 | 4 | 0,85 |
| Siamese | 4 | 4 | 0,85 |
| British Shorthair | 4 | 4 | 0,85 |
| Bombay | 3 | 4 | 0,85 |
| Ragdoll | 4 | 4 | 0,88 |
| Persian | 4 | 4 | 0,88 |

Os três casos sem detecção foram `Bombay_130`, `Sphynx_155` e `Sphynx_237`.
Eles devem entrar na revisão da fase de calibração; a avaliação acima não deve
ser reutilizada para ajustar limites, evitando que o conjunto de teste deixe de
ser independente.

Este benchmark mede apenas detecção e máscara. A precisão de cor e padrão de
pelagem ainda requer fotos próprias/autorizadas com revisão humana.
