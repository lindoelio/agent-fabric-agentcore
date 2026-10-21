# Ambiente local e origem do código

O projeto foi recuperado da conversa [Viabilidade do dcode no AWS](https://chatgpt.com/c/6ad7f1a4-58d0-83e8-9280-410c621827ab), no ChatGPT Work.
O nome original é `agent-factory-agentcore`; o diretório local solicitado é
`/Users/lindoelio/Projects/agent-fabric-agentcore`.

O download do ZIP não foi concluído pelo navegador. Os 31 arquivos foram recuperados
dos registros completos de criação e alteração da tarefa
`01a15b88-2f60-75db-b21f-8b7fc4d3300e`, em ordem, com conferência dos trechos anteriores
em cada atualização. O commit local `f632fe2` preserva essa importação: 31 arquivos e
1.859 linhas, os mesmos totais do commit original `f691eff` registrados na conversa.
O histórico Git remoto e o ZIP não foram transferidos.

Depois da importação, foi ajustada a expectativa de caminho de um teste para o macOS
(`/tmp` resolve para `/private/tmp`) e aplicada a formatação padrão do Terraform.
O arquivo `infra/.terraform.lock.hcl` fixa o provider instalado.

## Ambiente preparado

- `.venv`: Python 3.12.11, pacote local editável e extras `agent,client` instalados.
- dcode 0.1.87 e AWS provider 6.69.1.
- `infra/.terraform`: inicialização concluída com backend desabilitado.
- `.local-state/objects`: exemplo `software/v001` empacotado localmente.
- `.local-state/import/source-changes.json`: registros usados na recuperação.
- `.local-state/import/installed-requirements.txt`: versões instaladas nesta máquina.

Os caminhos `.venv`, `.terraform` e `.local-state` estão ignorados pelo Git.

## Verificações executadas

```bash
source .venv/bin/activate
PYTHONPATH=src FACTORY_TEST_REAL_DCODE=1 python -m unittest discover -s tests -v
terraform -chdir=infra fmt -check
terraform -chdir=infra validate
uv pip check --python .venv/bin/python
```

Resultado: 12 testes passaram, incluindo inicialização ACP, criação de sessão e
retomada com o dcode real, sem enviar prompt ao modelo. Terraform válido e
dependências compatíveis. O serviço Uvicorn também foi iniciado em loopback;
`GET /ping` respondeu `Healthy` e `POST /invocations` com `action=describe` retornou
o protocolo esperado. O servidor foi encerrado após essa verificação.

Build da imagem Docker, autenticação AWS, invocação de modelo, publicação no S3/ECR,
provisionamento e publicação no GitHub ainda não foram executados nesta importação.

## Retomar o desenvolvimento

```bash
source .venv/bin/activate
FACTORY_LOCAL_DEFINITIONS="$PWD/.local-state/objects" \
  FACTORY_STATE_ROOT="$PWD/.local-state/session" \
  uvicorn agent_factory.app:app --host 127.0.0.1 --port 8080 --no-access-log
```

Em outro terminal, ative a mesma `.venv`. O cliente é `agent-factory`; os comandos
de implantação e uso estão no [README](../README.md). Uma conversa real com o modelo
depende de credenciais e acesso Bedrock e gera consumo na AWS.
