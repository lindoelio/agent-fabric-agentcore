# Ambiente local e origem do código

O projeto foi iniciado no ChatGPT Work como `agent-factory-agentcore` e importado
para este repositório como `agent-fabric-agentcore`. A importação preservou 31 arquivos
e 1.859 linhas de código e documentação. O histórico de origem e a evidência de
recuperação permanecem no ambiente local do autor.

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

Resultado da configuração inicial: 12 testes passaram, incluindo inicialização ACP, criação de sessão e
retomada com o dcode real, sem enviar prompt ao modelo. Terraform válido e
dependências compatíveis. O serviço Uvicorn também foi iniciado em loopback;
`GET /ping` respondeu `Healthy` e `POST /invocations` com `action=describe` retornou
o protocolo esperado. O servidor foi encerrado após essa verificação.

Essa configuração inicial foi seguida da revisão para publicação. Os resultados
atuais e as validações ainda pendentes estão em [validation.md](validation.md).

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
