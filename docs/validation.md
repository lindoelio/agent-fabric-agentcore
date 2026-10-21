# Validação para publicação

Verificação executada em 21 de outubro de 2026, antes da publicação pública.

## Resultados observados

| Verificação | Resultado |
|---|---|
| Python 3.12 no macOS, suíte completa com opcionais habilitados | 16 testes passaram |
| dcode 0.1.87 real | Inicialização ACP, nova sessão e retomada após reiniciar o processo passaram |
| Uvicorn + WebSocket + cliente ACP | Aprovação negada, resposta e retomada passaram com agente fixture |
| Cabeçalhos produzidos pelo SDK AgentCore | Handshake padrão passou após remover cabeçalhos duplicados |
| Mensagens grandes com Unicode | Fragmentação e remontagem pelo cliente passaram |
| Integridade e isolamento das releases | Hashes, vínculo de versão, caminhos, symlinks e publicação idempotente testados |
| Terraform | `fmt -check` e `validate` passaram com AWS provider 6.69.1 |
| Docker ARM64 | Imagem construída e carregada localmente |
| Suíte dentro da imagem ARM64, como usuário não root e sem rede externa | Os mesmos 16 testes passaram |
| Entrypoint padrão da imagem | `/ping` respondeu `Healthy`; container encerrou normalmente |
| Arquivos e histórico Git anteriores à publicação | Varredura de padrões de credenciais sem ocorrências; estado, perfis e credenciais excluídos pelo Git |

A validação de cabeçalhos usa o SDK instalado e credenciais fictícias. Ela verifica
o handshake e a preservação dos cabeçalhos de autenticação; não comprova aceitação
da assinatura pela AWS. O teste de dcode real não envia prompts a modelos. Os
testes de ferramentas, aprovações e respostas usam um subprocesso fixture.

## Correções da revisão

- O cliente passava os cabeçalhos `Upgrade`, `Connection`, `Host` e `Sec-WebSocket-*`
  do SDK como cabeçalhos adicionais. A biblioteca WebSocket já os gera. A duplicação
  causava rejeição de handshake (HTTP 426 no servidor local). O cliente agora mantém
  os cabeçalhos de autenticação e deixa a biblioteca gerar o handshake.
- O empacotador passou a rejeitar também um diretório `profile` que seja um symlink.
  Antes, a verificação abrangia somente os caminhos encontrados dentro dele.
- A leitura de arquivos para empacotamento passou a respeitar o limite antes de
  carregar um arquivo inteiro na memória.
- A CI passou a verificar formatação Terraform sem alterá-la e a executar testes
  dentro da imagem ARM64, com `--network none`.

## Reproduzir

```bash
source .venv/bin/activate
PYTHONPATH=src FACTORY_TEST_REAL_DCODE=1 FACTORY_TEST_TRANSPORT=1 \
  python -m unittest discover -s tests -v
terraform -chdir=infra init -backend=false
terraform -chdir=infra fmt -check
terraform -chdir=infra validate
docker buildx build --platform linux/arm64 --provenance=false \
  --load -t agent-fabric-agentcore:verify .
docker run --rm --network none \
  -v "$PWD/tests:/validation/tests:ro" \
  -v "$PWD/examples:/validation/examples:ro" \
  -e PYTHONPATH=/validation/tests \
  -e FACTORY_TEST_REAL_DCODE=1 -e FACTORY_TEST_TRANSPORT=1 \
  -e AWS_EC2_METADATA_DISABLED=true \
  agent-fabric-agentcore:verify \
  python -m unittest discover -s /validation/tests -v
```

## Validação AWS pendente

Não foram executados `terraform apply`, publicação S3/ECR, criação de Runtime,
invocação de modelo ou testes autenticados contra AgentCore. Antes de usar em produção:

1. Revisar o plano Terraform e provisionar em uma conta/região de teste.
2. Publicar imagem por digest e release de exemplo.
3. Verificar escrita no storage montado com o usuário da imagem.
4. Verificar conexão IAM/SigV4, acesso ao modelo, resposta, ferramenta e aprovação.
5. Verificar desconexão, encerramento de subprocessos e retomada após stop/resume.
6. Verificar expiração e comportamento após atualizar a versão do Runtime.

O MVP pressupõe operadores confiáveis. Não há autorização por tenant, backup durável
automático, reconexão automática ou execução em background após desconexão. Os
limites de persistência estão descritos no README. A publicação pública do código
não equivale a um serviço AWS implantado ou a prontidão para produção.
