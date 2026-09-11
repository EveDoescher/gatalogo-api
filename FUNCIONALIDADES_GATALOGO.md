# Funcionalidades do Gatálogo

## Visão do produto

O Gatálogo é um aplicativo mobile para registrar, conhecer e acompanhar gatos
vistos no mundo real. Ele combina uma coleção pessoal gamificada, análise de
imagem, funcionamento offline, sincronização privada e uma camada social. A
identificação visual serve para sugerir que dois registros podem retratar o
mesmo gato; ela nunca deve ser apresentada como certeza absoluta.

O aplicativo não é um reconhecimento em tempo real da câmera e não tenta
adivinhar raça, cor exata, padrão ou a identidade de um gato apenas para
exibição. A análise detalhada continua sendo responsabilidade da API.

## Cadastro de gatos

### Captura de foto

A pessoa fotografa um gato pela câmera do aplicativo. A galeria não faz parte
do fluxo principal de captura: a proposta é incentivar registros genuínos de
avistamentos. A foto principal é mantida como a representação visual do gato na
biblioteca.

### Pré-filtro local de qualidade

Antes do envio, o aplicativo avalia a imagem no celular. Ele descarta fotos
claramente escuras, claras demais, sem contraste, muito borradas, sem gato em
destaque, com o gato distante ou com mais de um gato importante na cena. O
detector local só aceita gato com confiança mínima de 45% e área mínima de 5%
da imagem; um segundo gato com ao menos 35% da área do principal bloqueia a
foto. Falhas técnicas do filtro nunca impedem o uso: nesse caso, a imagem segue
para a API.

### Análise visual

Depois de aprovada, a foto vai para a API para segmentação, análise de
pelagem, cores visíveis, padrão visual e classificação final. O resultado fica
associado ao gato e pode ser consultado nos detalhes. A análise não substitui
uma avaliação veterinária e não promete identificar um gato individual.

### Nome, edição e exclusão

Cada gato pode receber um nome escolhido pela pessoa e esse nome pode ser
editado. A exclusão é sempre uma ação explícita da pessoa; o aplicativo nunca
remove automaticamente um gato que falhou na análise. Exclusões feitas offline
viram registros de remoção sincronizáveis, para que o gato não reapareça em
outro aparelho.

### Fotos complementares

Além da foto principal, é possível acrescentar referências de frente, lado
esquerdo, lado direito e costas. Elas permanecem privadas, podem ser removidas
pela pessoa e ajudam a melhorar a análise de aparência e a comparação futura
entre registros. Essas fotos também entram na fila offline quando necessário.

## Coleção e gamificação

### Biblioteca de gatos

A biblioteca reúne todos os gatos catalogados pela pessoa, com foto, nome,
estado da análise e informações principais. Ela funciona como um álbum pessoal
de descobertas e preserva o histórico de cada registro.

### Progresso de coleção

O progresso transforma o ato de observar gatos em uma coleção: cada novo gato
catalogado, cada análise concluída e cada registro enriquecido contribui para o
avanço da pessoa. O objetivo é estimular fotos melhores e registros mais úteis,
não competição por quantidade.

### Conquistas

Conquistas reconhecem marcos de uso, por exemplo primeiros gatos catalogados,
coleção crescente, consistência de registros, contribuição de fotos
complementares e participação solidária em avistamentos. Elas podem ser
compartilhadas no feed de amigos, respeitando as preferências de privacidade.

## Localização e funcionamento offline

### Localização do avistamento

Quando autorizada, a localização é registrada no momento da foto e independe de
Wi-Fi ou dados móveis: GPS pode funcionar sem internet. A posição é usada para
mostrar onde o gato foi visto e para cruzar avistamentos com alertas de gatos
desaparecidos. O mapa utiliza OpenStreetMap, sem chave de Google Maps.

### Catálogo offline

Fotos, dados e alterações locais continuam disponíveis sem rede. Um gato
fotografado offline é salvo localmente e fica aguardando conexão para envio e
análise. O aplicativo não deixa a foto presa em carregamento infinito.

### Fila de análise

A fila persistente envia fotos quando houver rede. Cada análise tem no máximo
duas tentativas automáticas; depois disso, a pessoa decide entre tentar
novamente ou excluir o registro. Assim, uma falha de API não gera requisições
infinitas.

### Sincronização entre aparelhos

Ao entrar em uma conta, o catálogo local é associado ao usuário e sincronizado
com o servidor. Em outro celular ou após reinstalação, a coleção, análises e
fotos privadas são baixadas novamente. O servidor usa data de edição em UTC e
ID do dispositivo para resolver conflitos por última edição; remoções vencem
alterações anteriores.

## Conta e segurança

### Cadastro e entrada por e-mail

A pessoa pode criar uma conta com e-mail e senha. O e-mail é confirmado por OTP
de seis dígitos, com validade curta e número limitado de tentativas. A tela de
OTP mostra somente as caixas de código; o e-mail já pertence à etapa anterior.

### Entrada com Google

Também é possível entrar com Google. A API valida o ID token, a audiência, o
emissor e a confirmação do e-mail. Nome, avatar e outros dados de perfil Google
não são armazenados.

### Senhas e sessões

Senhas são armazenadas apenas como hash Argon2id, nunca em texto puro. A sessão
usa access token de curta duração e refresh token rotativo. Os tokens ficam no
armazenamento seguro do celular, não no SQLite nem em logs. Logout, troca de
senha e desativação da conta revogam as sessões correspondentes.

### Recuperação e desativação

O fluxo de redefinição de senha usa o mesmo modelo seguro de OTP por e-mail. A
desativação é reversível: as sessões são revogadas e o acesso é bloqueado, mas
os dados são preservados para recuperação pela mesma conta verificada.

### Privacidade das fotos

As fotos ficam em armazenamento privado do servidor, organizadas por usuário.
O banco guarda somente chaves, hash e metadados das imagens; os bytes não ficam
no PostgreSQL. Download, edição e remoção exigem sessão autenticada e validação
de propriedade.

## Amigos e comunidade

### Perfil social mínimo

Cada conta escolhe um identificador de usuário único. O perfil social evita
coletar nome real, foto de perfil ou outros dados pessoais desnecessários.

### Convites de amizade

Amizades não são automáticas. Uma pessoa envia um convite dentro do aplicativo,
a outra aceita ou recusa e só então ambas se tornam amigas. Convites pendentes
podem ser consultados e não concedem acesso ao conteúdo privado.

### Feed de amigos

Amigos compartilham um feed de atividades permitidas, como novas conquistas,
marcos de coleção e contribuições públicas escolhidas pela pessoa. Fotos e
localizações privadas não devem ser expostas pelo feed sem uma ação consciente
de compartilhamento.

### Conversas

Amigos podem conversar dentro do aplicativo. Quando houver uma possível relação
entre um avistamento e um gato desaparecido, a conversa facilita o contato entre
quem viu e quem procura, especialmente quando já existe amizade.

### Notificações

O aplicativo mantém notificações internas para convites, aceite de amizade,
conquistas, possível correspondência e novas mensagens. Notificações do sistema
podem ser enviadas quando o serviço de push estiver configurado.

## Gatos desaparecidos e avistamentos

### Meus gatos

**Meus gatos** é uma área própria para os gatos da pessoa, separada da
biblioteca de gatos avistados e catalogados na rua. Antes de qualquer alerta, a
pessoa cadastra voluntariamente seu gato, escolhe nome e mantém um perfil de
reconhecimento mais completo. Esse perfil representa um gato conhecido e não um
avistamento casual.

### Perfil de reconhecimento do gato próprio

O perfil de cada gato próprio aceita uma foto principal e várias fotos de
referência, orientadas por ângulo: frente, lado esquerdo, lado direito e costas.
Também pode receber novas fotos ao longo do tempo. Cada referência é segmentada
e analisada quando houver conexão; as evidências são reunidas em um conjunto de
vetores de aparência. Quanto mais ângulos e condições de luz legítimas forem
registrados, melhor será a capacidade de encontrar candidatos compatíveis no
futuro. As fotos continuam privadas e sincronizadas entre aparelhos.

### Alerta de desaparecimento

A pessoa só pode abrir um alerta a partir de um gato previamente registrado em
**Meus gatos**. O alerta reutiliza toda a foto principal, referências de vários
ângulos e vetores já preparados desse perfil, além de reunir último ponto
conhecido, raio de busca e estado do caso. Ele pode ser encerrado quando o gato
for encontrado ou deixar de precisar de busca.

### Registro de avistamento

Um gato já catalogado pode ser registrado como avistamento, com local, data e
observação. Isso permite que um registro comum se torne uma possível pista sem
forçar a pessoa a abrir um alerta próprio.

### Cruzamento por aparência e distância

O sistema só sugere uma correspondência quando dois critérios se combinam:
semelhança visual e distância dentro do raio definido para o alerta. Uma foto
parecida fora da área de busca não deve acionar o alerta sozinha, e uma foto
dentro da área sem semelhança suficiente também não deve ser tratada como pista.

### Sugestões, não confirmação automática

O resultado é uma sugestão com nível de confiança, fotos comparadas e distância
do último local conhecido. A pessoa que procura decide se aquela pista merece
contato ou investigação. O sistema não declara automaticamente que um gato foi
encontrado nem expõe localização exata desnecessariamente.

### Casos de pelagem sólida

Gatos de uma cor só produzem mais falsos positivos visuais. Para eles, o sistema
aplica um limiar de semelhança mais alto, dá mais peso a múltiplas fotos e exige
que a proximidade geográfica seja coerente. Uma imagem isolada de um gato preto
ou branco nunca deve bastar para uma correspondência forte.

## Comparação e reconhecimento visual

### Segmentação do gato

Para comparação, um detector encontra o gato e o SAM 2 cria uma máscara da
instância principal. O fundo é neutralizado antes de criar o vetor, reduzindo a
influência de calçada, parede, vegetação e iluminação do cenário.

### Embeddings DINOv2

O DINOv2 transforma o recorte segmentado em um vetor numérico de aparência. O
vetor não é uma identificação biométrica definitiva: ele é uma representação
visual usada para localizar candidatos semelhantes.

### Busca vetorial

Os vetores são mantidos no PostgreSQL com pgvector e índice HNSW. Ao chegar uma
foto nova, o sistema busca as candidatas mais semelhantes antes de aplicar as
regras de localização, confiança e pelagem sólida.

### Processamento em segundo plano

A criação de máscara e vetor acontece em worker separado, para não bloquear o
envio normal de foto ou a API. No ambiente local atual, SAM 2 e DINOv2 podem
rodar em CPU; por isso o processamento é lento e opcional. Em uma infraestrutura
com GPU compatível, o mesmo worker pode ser acelerado posteriormente.

### Benchmark isolado

O projeto contém um teste isolado que usa fotos reais já existentes, mais fotos
de comparação locais. Ele monta uma galeria temporária, recorta com SAM 2, cria
os vetores DINOv2 e mede Top-1 e Recall@K no pgvector. O benchmark não altera
usuários, gatos, alertas ou embeddings reais do catálogo.

## Telas do aplicativo

### Entrada e cadastro

Apresenta a escolha entre entrar, criar conta com e-mail e senha ou usar Google.
Também dá acesso à recuperação de senha.

### Verificação de e-mail

Exibe as seis caixas do OTP, o estado de envio do código e opções para receber
outro código quando permitido. Não pede o e-mail novamente.

### Início

Resume a coleção, o estado de sincronização, atividades importantes e atalhos
para capturar um gato, abrir a biblioteca, ver alertas e acessar amigos.

### Capturar gato

Abre a câmera, obtém localização quando autorizada e conduz a revisão da foto.
Mostra a mensagem de conferência de enquadramento e qualidade enquanto o
pré-filtro local executa. Só libera o uso da foto se ela for aprovada ou se o
pré-filtro tiver falha técnica.

### Revisão e envio da foto

Mostra a foto capturada, o resultado do pré-filtro, o estado de envio e as
mensagens de reprovação quando aplicável. Sem rede, confirma que o gato foi
salvo para sincronização posterior.

### Biblioteca

Exibe a coleção pessoal de gatos, incluindo foto, nome, estado da análise e
estado de sincronização. É o ponto de entrada para detalhes, edição e exclusão.

### Meus gatos

Exibe somente os gatos da própria pessoa, com estado do perfil de
reconhecimento, quantidade de fotos de referência por ângulo e indicação de
preparo para uma eventual busca. É separado da biblioteca de gatos avistados.

### Cadastro e detalhes de gato próprio

Permite criar e manter o perfil de um gato da pessoa: nome, foto principal e
fotos adicionais de frente, lados e costas. Mostra quais ângulos ainda faltam,
o estado de análise das referências e a ação para declarar o gato desaparecido.

### Detalhes do gato

Mostra foto principal, nome editável, análise visual, localização do registro,
estado da fila e ações para ver fotos, acrescentar referências, excluir, marcar
como desaparecido ou registrar um avistamento.

### Galeria privada do gato

Permite abrir a foto principal e as fotos complementares, identificar o ângulo
de cada referência e remover somente as imagens que a pessoa não quer manter.

### Alertas de desaparecidos

Lista os alertas ativos e concluídos derivados de **Meus gatos**. Permite abrir
um alerta, criar ou encerrar a busca e consultar sugestões de avistamento.

### Mapa e registro de avistamento

Permite escolher ou ajustar um ponto no mapa OpenStreetMap, definir o raio de
busca de um alerta e registrar onde o gato foi visto. A localização é apresentada
como informação sensível e usada apenas para o contexto da busca.

### Amigos

Exibe o identificador social, convites recebidos e enviados, lista de amigos e
o feed compartilhado. Também permite iniciar uma conversa com um amigo.

### Conversa

Apresenta mensagens privadas entre duas pessoas amigas e, quando aplicável,
contexto de uma possível correspondência de gato desaparecido.

### Notificações

Centraliza convites, mensagens, conquistas, progresso de sincronização e
sugestões de avistamento. Esta tela é o complemento da entrega por push quando
ela estiver configurada.

### Conta e configurações

Mostra e-mail da conta, identificador social, opções de sessão, preferências de
privacidade, sincronização, mudança/recuperação de senha, logout e desativação
reversível da conta.
