# Protocolo de acesso

O serviço implementa ASGI. Em produção o AgentCore autentica a conexão por IAM.
Um cliente de editor ACP via stdio não se conecta diretamente a este endpoint:
é necessária uma ponte cliente entre stdio e este WebSocket. O CLI incluído já
implementa um cliente de terminal.

## HTTP

- `GET /ping`: `200` com `status` (`Healthy` ou `HealthyBusy`) e
  `time_of_last_update`. Não exige storage montado ou chamadas AWS.
- `POST /invocations`, corpo `{"action":"describe"}`: descreve o transporte.
  Não inicia tarefas. Outros valores de `action` retornam `400`.

## WebSocket `/ws`

1. Abra uma conexão AgentCore autenticada, com runtime session ID próprio.
2. Envie a seleção da release:

```json
{"type":"factory.open","team":"software","version":"v001"}
```

3. Receba `factory.ready` com `team`, `version`, `manifest_sha256` e `workspace`.
   Isso confirma carregamento e criação do processo, não uma chamada bem-sucedida
   ao modelo. A inicialização ACP confirma que o dcode está pronto.
4. Envie JSON-RPC ACP `initialize` com `protocolVersion: 1`.
5. Envie `session/new` ou `session/load`. Guarde o `sessionId` retornado.
6. Envie `session/prompt` e consuma notificações `session/update` até a resposta.
7. Responda a solicitações `session/request_permission` com o ID original.
   Não bloqueie a leitura enquanto uma aprovação está pendente.

Mensagens ACP regulares são objetos JSON-RPC 2.0 sem envelope adicional, um por frame.
`cwd` é substituído pelo workspace remoto; MCPs fornecidos pelo cliente são removidos.
Capacidades de arquivos e terminal no cliente são desabilitadas: as ferramentas do
dcode operam no servidor. `session/cancel` é encaminhado ao dcode.

Mensagens maiores de saída são divididas:

```json
{"type":"factory.chunk","id":"transfer-id","index":0,"total":3,"data":"fragmento da string JSON original"}
```

Concatene `data` em ordem, verificando `id` e `index`, e interprete o resultado como
um único objeto JSON. Frames de entrada não são divididos: limite de 60 KiB. Referencie
arquivos no workspace para entradas maiores. O cliente incluído remonta a saída.

`factory.error` encerra a execução da conexão. Não reenvie automaticamente prompts
após erros ou desconexão: uma ferramenta pode ter executado antes de a resposta cair.
Reconecte com o mesmo runtime session ID, a mesma release e `session/load` para
inspecionar o estado salvo antes de decidir continuar.

Uma segunda conexão na mesma sessão recebe fechamento `1008`. Encerrar uma conexão
para o dcode e seu grupo de processos. A recuperação depende da disponibilidade dos
checkpoints e do session storage, conforme os limites descritos no README.
