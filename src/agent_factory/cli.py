"""Publish immutable S3 releases and chat with an AgentCore-hosted dcode."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from agent_factory.bundles import DefinitionError, identifier, pack


def websocket_headers(headers: dict[str, str]) -> dict[str, str]:
    """Preserve authentication; let websockets generate its own handshake headers.

    AgentCore's SDK supplies these as unsigned headers. Passing them through
    additional_headers duplicates Upgrade/Connection/Key and breaks the handshake.
    Host is generated from the same signed runtime URL by websockets.
    """
    owned = {"host", "connection", "upgrade", "user-agent"}
    return {key: value for key, value in headers.items()
            if key.lower() not in owned and not key.lower().startswith("sec-websocket-")}


def publish(client, bucket: str, prefix: str, files: dict[str, bytes]) -> None:
    """Conditional writes permit idempotent retries, never overwrite a release."""
    for relative, content in files.items():
        key = f"{prefix}/{relative}"
        try:
            client.put_object(Bucket=bucket, Key=key, Body=content, IfNoneMatch="*")
        except Exception as exc:
            if getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise
            response = client.get_object(Bucket=bucket, Key=key)
            stream = response["Body"]
            try:
                if stream.read(len(content) + 1) != content:
                    raise DefinitionError("Release already exists with different contents; choose a new version.") from exc
            finally:
                stream.close()


class ACPClient:
    def __init__(self, ws):
        self.ws = ws
        self.counter = 0

    async def receive(self):
        value = json.loads(await self.ws.recv())
        if value.get("type") != "factory.chunk":
            return value
        chunks = [value]
        for index in range(1, value["total"]):
            item = json.loads(await self.ws.recv())
            if item.get("id") != value["id"] or item.get("index") != index:
                raise RuntimeError("Invalid chunk sequence.")
            chunks.append(item)
        return json.loads("".join(item["data"] for item in chunks))

    async def call(self, method: str, params: dict):
        self.counter += 1
        request_id = self.counter
        await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
        while True:
            message = await self.receive()
            if message.get("type") == "factory.error":
                raise RuntimeError(message["detail"])
            if message.get("method") == "session/request_permission":
                options = message["params"]["options"]
                print("\nTool approval:", json.dumps(message["params"]["toolCall"], ensure_ascii=False), file=sys.stderr)
                for index, option in enumerate(options, 1):
                    print(f"{index}. {option['name']}", file=sys.stderr)
                answer = await asyncio.to_thread(input, "Select option (Enter denies): ")
                try:
                    index = int(answer) - 1
                    selected = options[index] if 0 <= index < len(options) else None
                except ValueError:
                    selected = None
                outcome = ({"outcome": "selected", "optionId": selected["optionId"]}
                           if selected else {"outcome": "cancelled"})
                await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"outcome": outcome}}))
            elif message.get("method") == "session/update":
                update = message.get("params", {}).get("update", {})
                if update.get("sessionUpdate") == "agent_message_chunk":
                    print(update.get("content", {}).get("text", ""), end="", flush=True)
            elif "method" in message and "id" in message:
                await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                    "error": {"code": -32601, "message": "Client method not supported"}}))
            elif message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(json.dumps(message["error"]))
                return message["result"]


async def chat(args):
    import websockets

    headers = {}
    if args.local_url:
        url = args.local_url
    else:
        from bedrock_agentcore.runtime import AgentCoreRuntimeClient

        if not args.runtime_arn:
            raise DefinitionError("Pass --runtime-arn or --local-url.")
        runtime_session = args.runtime_session_id or str(uuid.uuid4())
        print(f"Runtime session: {runtime_session}", file=sys.stderr)
        url, headers = AgentCoreRuntimeClient(region=args.region).generate_ws_connection(
            runtime_arn=args.runtime_arn, session_id=runtime_session
        )
    async with websockets.connect(url, additional_headers=websocket_headers(headers), max_size=64 * 1024) as ws:
        client = ACPClient(ws)
        await ws.send(json.dumps({"type": "factory.open", "team": args.team, "version": args.version}))
        ready = await client.receive()
        if ready.get("type") != "factory.ready":
            raise RuntimeError(ready)
        await client.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        params = {"cwd": ready["workspace"], "mcpServers": []}
        if args.resume:
            params["sessionId"] = args.resume
            await client.call("session/load", params)
            session_id = args.resume
        else:
            result = await client.call("session/new", params)
            session_id = result["sessionId"]
        print(f"ACP session: {session_id}\nUse the same runtime ID and --resume {session_id} to reconnect.", file=sys.stderr)
        while True:
            prompt = await asyncio.to_thread(input, "\nYou> ")
            if prompt.strip() in {"/quit", "/exit"}:
                return
            if not prompt.strip():
                continue
            await client.call("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": prompt}]})
            print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("pack", "publish"):
        command = sub.add_parser(name)
        command.add_argument("source", type=Path)
        command.add_argument("--team", required=True)
        command.add_argument("--version", required=True)
        command.add_argument("--model", required=True)
        command.add_argument("--prefix", default="teams")
        if name == "pack":
            command.add_argument("--output", type=Path, required=True)
        else:
            command.add_argument("--bucket", required=True)
    command = sub.add_parser("chat")
    command.add_argument("--team", required=True)
    command.add_argument("--version", required=True)
    command.add_argument("--runtime-arn")
    command.add_argument("--runtime-session-id")
    command.add_argument("--region", default="us-west-2")
    command.add_argument("--resume")
    command.add_argument("--local-url")
    args = parser.parse_args()
    try:
        if args.command == "chat":
            asyncio.run(chat(args))
            return
        identifier(args.team)
        identifier(args.version)
        # Reuse loader's prefix validation before constructing publisher keys.
        from agent_factory.bundles import BundleLoader

        BundleLoader(None, args.prefix)
        raw, files = pack(args.source, args.team, args.version, args.model)
        prefix = f"{args.prefix.rstrip('/')}/{args.team}/releases/{args.version}"
        objects = {**files, "manifest.json": raw}  # Commit manifest last.
        if args.command == "pack":
            destination = args.output / prefix
            if destination.exists():
                raise DefinitionError("Output release already exists; choose a new output or version.")
            for name, content in objects.items():
                path = destination / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            print(destination)
        else:
            import boto3

            publish(boto3.client("s3"), args.bucket, prefix, objects)
            print(f"Published {args.team}/{args.version}")
    except (DefinitionError, RuntimeError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    except (KeyboardInterrupt, EOFError):
        pass
