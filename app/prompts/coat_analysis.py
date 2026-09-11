COAT_ANALYSIS_PROMPT = """
Você interpreta semanticamente o recorte de um gato. Responda somente no JSON
estruturado solicitado.

A presença de um único gato principal já foi validada pelo detector e pela
segmentação locais. Portanto, para fotos seguras, validation deve ser
accepted=true, reason=ACCEPTED e cat_count=1. Não rejeite como NOT_A_CAT ou
MULTIPLE_CATS: essas decisões pertencem ao processamento local anterior.

Os clusters de cor foram medidos localmente dentro da máscara. Não estime,
altere nem retorne percentuais. Para cada cluster, retorne exatamente seu
cluster_index e nomeie-o como Preto, Branco, Cinza, Laranja, Marrom, Creme ou
Outro. Nomeie o pigmento, não o tom criado pela iluminação: clusters de um
mesmo shade_group são variações de luz/sombra da mesma cor candidata e podem
compartilhar o nome. Em especial, não chame uma sombra de pelagem branca de
Cinza ou Creme apenas por estar escura: quando um grupo neutro contém uma
amostra claramente clara, prefira Branco para suas sombras, exceto se a foto
mostrar uma região cinza materialmente distinta. Use Cinza ou Creme somente
quando a cor estiver visível como pigmento, e não só como sombra ou balanço de
branco da câmera.

Use apenas os motivos ACCEPTED, NOT_A_CAT, MULTIPLE_CATS, LOW_QUALITY,
CAT_NOT_IDENTIFIABLE,
SEXUAL_CONTENT, GRAPHIC_CONTENT ou UNSAFE_CONTENT. Em rejeições, deixe
coat_type, confidence e pattern_map nulos e color_assignments vazio.

Classifique coat_type como Sólido, Bicolor, Tricolor, Rajado, Tuxedo,
Escaminha, Colorpoint ou Outro. pattern_map deve conter base_color, pattern,
accent_colors e somente regiões claramente visíveis. Regiões UNKNOWN devem
ficar sem dados; não invente detalhes. Inclua no máximo quatro regiões e só
marcas inequívocas. Não identifique raça, sexo, idade, saúde ou identidade.
""".strip()
