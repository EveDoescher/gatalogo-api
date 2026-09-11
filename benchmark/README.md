# Benchmark público inicial

Este diretório guarda a seleção e os resultados reprodutíveis usados para
validar a etapa local de **detecção e segmentação**. Ele não mede ainda a
classificação de cor/padrão, pois o Oxford-IIIT Pet não oferece esses rótulos.

## Fonte selecionada

- **Oxford-IIIT Pet Dataset** — contém fotos de gatos em 12 raças, junto de
  trimaps por pixel. A seleção é estratificada por raça para reduzir o viés de
  escolher só aparências semelhantes.
- Licença: **CC BY-SA 4.0**. Mantenha a atribuição ao Oxford-IIIT Pet Dataset e
  a mesma licença ao redistribuir adaptações do conjunto.
- Página e downloads oficiais:
  <https://www.robots.ox.ac.uk/~vgg/data/pets/>.

O primeiro recorte possui 12 imagens por raça: 8 para `calibration` e 4 para
`evaluation`, somando **144 imagens**. A calibração serve apenas para ajustar
limiares operacionais; números de qualidade devem ser reportados sempre sobre
o split `evaluation`, que fica sem consulta durante os ajustes.

## Preparar o conjunto

Baixe e extraia os dois arquivos oficiais de imagens e anotações de modo que a
estrutura local seja:

```text
data/raw/oxford-iiit-pet/
├── images/
└── annotations/
    ├── list.txt
    └── trimaps/
```

Em seguida, na raiz do projeto, gere o manifesto:

```powershell
python scripts/prepare_oxford_pet_benchmark.py
```

O manifesto versionável aparece em
`benchmark/manifests/oxford_iiit_pet_cats_v1.csv`; imagens e resultados são
ignorados pelo Git.

## Executar a avaliação

Com o checkpoint do SAM 2 já configurado, rode:

```powershell
python scripts/evaluate_oxford_pet_segmentation.py --split evaluation
```

O relatório JSON contém:

- `segmentation_rate`: proporção de fotos nas quais houve máscara utilizável;
- `iou_strict`: sobreposição com o núcleo do animal anotado;
- `iou_inclusive`: sobreposição considerando também a borda incerta do pelo;
- `p10`: pior faixa de 10% dos casos, importante para enxergar falhas menos
  comuns que a média esconde.

A primeira execução validada está registrada em
[`BASELINE.md`](BASELINE.md). Ela deve servir como comparação para futuras
mudanças no detector, no SAM 2 ou nos limites operacionais.

Após qualquer mudança em `DETECTOR_CONFIDENCE`,
`SECONDARY_CAT_MAX_AREA_RATIO` ou no modelo, execute primeiro `calibration` e
registre a configuração escolhida; então execute `evaluation` uma única vez
para obter a comparação final.

## Próxima camada: fotos do catálogo

O conjunto público valida a máscara, mas não oferece verdade de referência
para percentuais de pelagem. Para essa fase, crie depois um manifesto separado
com fotos próprias/autorizadas e revisão humana de cor principal, cores
secundárias, padrão e percentuais visíveis.
