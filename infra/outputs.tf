output "definitions_bucket" {
  value = aws_s3_bucket.definitions.id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.runtime.repository_url
}

output "runtime_arn" {
  value = try(aws_bedrockagentcore_agent_runtime.factory[0].agent_runtime_arn, null)
}

output "runtime_role_arn" {
  value = aws_iam_role.runtime.arn
}

output "caller_policy_json" {
  description = "Attach to the trusted identity that uses the client; not attached automatically."
  value = var.runtime_image_uri == null ? null : jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStream"]
      Resource = [
        aws_bedrockagentcore_agent_runtime.factory[0].agent_runtime_arn,
        "${aws_bedrockagentcore_agent_runtime.factory[0].agent_runtime_arn}/runtime-endpoint/*"
      ]
    }]
  })
}
