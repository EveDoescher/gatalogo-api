# Dependências — triagem de aplicabilidade pendente

O usuário autorizou explicitamente o envio do payload revisado com 254 nomes e versões
Python/Dart a `https://api.osv.dev/v1/querybatch` e a leitura dos avisos por ID em
`/v1/vulns/{id}`. Antes da autorização, a revisão automática bloqueou o envio; a consulta
foi realizada somente depois da resposta autorizadora. Não foram enviados código,
credenciais, dados de usuários nem caminhos locais. O SHA do payload está nos resultados.

Foram consultadas 254 dependências, sem erro de consulta. Houve correspondência em seis
pacotes: 72 IDs retornados, com os 72 metadados lidos, agrupados em 50 componentes de aliases.
Isso não significa 50 vulnerabilidades confirmadas no Gatálogo. Nenhum exploit foi executado.
A abrangência é a instalação local e o lock Dart registrado; não certifica um deployment,
SBOM de imagem final, dependências nativas nem configuração de release.

| Pacote instalado | Caminho observado / prioridade de triagem |
|---|---|
| Pillow 12.3.0 | Importado por pré-processamento, fotos, reconhecimento e mídia de pistas; conferir formato, limites e condições afetadas por aviso. |
| PyJWT 2.13.0 | Importado por autenticação e validação de sessão; confrontar algoritmos/configuração efetivos e pré-condições do aviso. |
| urllib3 2.7.0 | Dependência instalada; confirmar cadeia transitiva, presença em produção e chamadas alcançáveis antes de atribuir impacto. |
| certifi 2026.7.22 | Bundle de certificados instalado; comparar escopo/autoridades afetadas e transportes usados no deployment. |
| Mako 1.4.1 | Verificar presença nas ferramentas Alembic e em runtime; não foi demonstrado template remoto controlável por usuário. |
| hydra-core 1.3.5 | Verificar carga de configurações de visão/modelos e origem confiável; não foi demonstrada configuração controlável externamente. |

Próximos passos: revisar cada grupo e seus aliases/withdrawn, descrição e versões afetadas;
fixar caminho de dependência e ambiente real; classificar alcançabilidade/impacto; propor
atualização em etapa autorizada e revalidar os testes de contratos, mídia e autenticação.
Não alterar dependências automaticamente a partir de um resultado do scanner.

Metodologia: [OSV batch API](https://google.github.io/osv.dev/post-v1-querybatch/).
Os registros/links primários de cada aviso estão em `dependency_advisory_review.json`.
