# Comparação da calibração — fotos autorais

Execução realizada em 20 fotos de calibração, usando detector aberto + SAM 2
e Gemini.

## Resumo

| Medida | Resultado |
| --- | ---: |
| Fotos processadas | 20 |
| Respostas completas | 13 (65,0%) |
| Erros | 7 (35,0%) |
| Cor principal correta | 8/13 (61,5%) |
| Conjunto de cores exato | 4/13 (30,8%) |
| Padrão correto | 11/13 (84,6%) |
| Acerto conjunto (cor + cores + padrão) | 2/13 (15,4%) |

`Conjunto de cores exato` é um critério rígido: qualquer cor extra ou ausente
conta como divergência. Por isso ele não deve ser interpretado como “a cor foi
completamente errada” sem conferir a foto e a máscara.

## Erros de execução

- `IMG_20241119_194803.jpg`: mais de um gato principal;
- `IMG_20241127_001148.jpg`: mais de um gato;
- `IMG_20251126_152405.jpg`: nenhum gato detectado;
- `IMG_20251105_150034.jpg`, `IMG_20260213_162801.jpg`,
  `IMG_20260703_113924.jpg` e `IMG_20260708_160203.jpg`: falha na resposta do
  Gemini.

## Leitura inicial

O padrão foi a parte mais consistente. As divergências mais frequentes foram
nas cores: o modelo costuma introduzir tons próximos (por exemplo, cinza,
creme ou marrom) que não estavam na referência humana, ou escolher uma cor
principal diferente quando há iluminação noturna, filhotes ou pelagem
tricolor.

Antes de ajustar o modelo, revise as prévias de máscara geradas para as 13
respostas completas. Uma máscara incorreta contamina diretamente a medição de
cor; uma máscara correta com cor divergente aponta para calibração semântica do
Gemini.

O JSON completo, incluindo a resposta estruturada de cada foto, está em
`data/own-cats/calibration_results.json`. As prévias estão em
`outputs/own-cats-benchmark/masks/`.
