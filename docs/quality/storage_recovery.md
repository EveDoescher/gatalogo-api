# Recuperação de arquivos privados após falhas

Banco e filesystem têm transações distintas. Uma gravação completa pode sobreviver ao rollback do banco; isso não autoriza apagar imediatamente a chave: outra requisição pode reutilizar os mesmos bytes/hash e confirmar sua referência.

## Garantias e limites

- A gravação usa temporário no mesmo diretório e substituição atômica. Falha antes da substituição conserva o arquivo anterior e remove o temporário quando o processo continua vivo.
- Rollback do upload conserva as referências anteriores e não publica sucesso. O arquivo novo, completo e sem referência, pode permanecer.
- Encerramento abrupto pode deixar `.upload-*.tmp`. Substituição atômica não promete durabilidade contra perda de energia nem commit conjunto com PostgreSQL.
- Falha ao remover a versão antiga depois do commit também pode deixar um órfão. Esse caso permanece rastreado em RSC-03; repetir o upload não é uma política de coleta.

## Procedimento de reconciliação

Não executar coleta concorrente com uploads, workers, restaurações ou exclusões. Pausar todos os escritores de **todos os processos/réplicas**, mantendo uma janela de manutenção; somente idade do arquivo ou uma consulta momentânea não eliminam a corrida.

1. Registrar SHA da API, diretório absoluto de storage, horário, banco/schema e processos pausados. Fazer backup consistente do banco e copiar o storage para um arquivo de recuperação; registrar hashes. Nunca usar o storage de desenvolvimento nos experimentos da campanha.
2. Obter o conjunto de chaves referenciadas por `RemoteCat.photo_key`, `CatPhoto.storage_key`, `CatAvatar.preview_key` e todos os valores de `CatAvatar.texture_keys`. Incluir também os registros marcados como excluídos enquanto existirem no banco. Para esse procedimento, limitar o inventário às mídias de gatos em `users/<UUID>/cats/`; fotos de perfil, posts e conversas exigem inventário próprio e ficam excluídas.
3. Normalizar separadores legados e validar cada caminho com `PrivatePhotoStorage.absolute_path`. Comparar o inventário de arquivos com **todas** as referências, inclusive compartilhadas. Arquivo ausente mas referenciado é incidente de integridade: recuperar do backup, sem apagar a referência automaticamente.
4. Gerar primeiro uma lista de candidatos com caminho relativo, tamanho, mtime e SHA-256. Submeter somente arquivos sem referência à quarentena fora da raiz servida, preservando hierarquia e manifesto. Reconsultar referências e conferir tamanho/mtime/hash imediatamente antes de mover. Se algum escritor não puder ser comprovadamente pausado, permanecer apenas no inventário, sem mover.
5. Conferir novamente as referências e os bytes das fotos vigentes; reiniciar escritores. Conservar a quarentena e o backup até revisão e encerramento do incidente. Este procedimento não autoriza exclusão definitiva nem estabelece retenção arbitrária.

Essa é uma política operacional de recuperação, não um coletor automático implementado. Nenhuma coleta ou interrupção dos serviços do usuário foi realizada nesta campanha. DATA-02 verifica rollback, isolamento e identificação do órfão; a automação de coleta concorrente não integra sua aprovação.
