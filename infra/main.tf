data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  partition    = data.aws_partition.current.partition
  account_id   = data.aws_caller_identity.current.account_id
  runtime_name = replace(var.project_name, "-", "_")
  log_prefix   = "arn:${local.partition}:logs:${var.aws_region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/${local.runtime_name}-*"
}

resource "aws_s3_bucket" "definitions" {
  bucket        = "${var.project_name}-definitions-${local.account_id}-${var.aws_region}"
  force_destroy = false
}

resource "aws_s3_bucket_versioning" "definitions" {
  bucket = aws_s3_bucket.definitions.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "definitions" {
  bucket = aws_s3_bucket.definitions.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "definitions" {
  bucket                  = aws_s3_bucket.definitions.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "definitions" {
  bucket = aws_s3_bucket.definitions.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid = "RequireTLS", Effect = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.definitions.arn, "${aws_s3_bucket.definitions.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_ecr_repository" "runtime" {
  name                 = "${var.project_name}-runtime"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_iam_role" "runtime" {
  name = "${var.project_name}-runtime"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Action = "sts:AssumeRole"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:${local.partition}:bedrock-agentcore:${var.aws_region}:${local.account_id}:*" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "runtime" {
  role = aws_iam_role.runtime.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid = "ReadPublishedDefinitions", Effect = "Allow", Action = ["s3:GetObject"]
        Resource = ["${aws_s3_bucket.definitions.arn}/${var.definitions_prefix}/*"]
      },
      {
        Sid = "ECRToken", Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*"
      },
      {
        Sid = "PullRuntimeImage", Effect = "Allow"
        Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = [aws_ecr_repository.runtime.arn]
      },
      {
        Sid = "InvokeConfiguredModels", Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = concat(
          [for id in var.bedrock_model_ids : "arn:${local.partition}:bedrock:*::foundation-model/${id}"],
          [for id in var.inference_profile_ids : "arn:${local.partition}:bedrock:${var.aws_region}:${local.account_id}:inference-profile/${id}"]
        )
      },
      {
        Sid = "RuntimeLogGroups", Effect = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:DescribeLogStreams", "logs:PutResourcePolicy"]
        Resource = [local.log_prefix]
      },
      {
        Sid = "RuntimeLogEvents", Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${local.log_prefix}:log-stream:*"]
      },
      {
        Sid = "DescribeLogGroups", Effect = "Allow", Action = ["logs:DescribeLogGroups"]
        Resource = ["arn:${local.partition}:logs:${var.aws_region}:${local.account_id}:log-group:*"]
      }
    ]
  })
}

resource "aws_bedrockagentcore_agent_runtime" "factory" {
  count              = var.runtime_image_uri == null ? 0 : 1
  agent_runtime_name = local.runtime_name
  description        = "Versioned S3-defined dcode teams with ACP over WebSocket"
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = var.runtime_image_uri
    }
  }
  network_configuration {
    network_mode = "PUBLIC"
  }
  protocol_configuration {
    server_protocol = "HTTP"
  }
  lifecycle_configuration {
    idle_runtime_session_timeout = var.idle_timeout_seconds
    max_lifetime                 = var.max_lifetime_seconds
  }
  dynamic "filesystem_configuration" {
    for_each = var.session_storage_enabled ? [1] : []
    content {
      session_storage {
        mount_path = "/mnt/workspace"
      }
    }
  }
  environment_variables = {
    DEFINITIONS_BUCKET = aws_s3_bucket.definitions.id
    DEFINITIONS_PREFIX = var.definitions_prefix
    FACTORY_STATE_ROOT = "/mnt/workspace"
    AWS_REGION         = var.aws_region
    AWS_DEFAULT_REGION = var.aws_region
  }
  lifecycle {
    precondition {
      condition     = var.idle_timeout_seconds <= var.max_lifetime_seconds
      error_message = "Idle timeout cannot exceed maximum lifetime."
    }
    precondition {
      condition     = startswith(var.runtime_image_uri, "${aws_ecr_repository.runtime.repository_url}@sha256:")
      error_message = "The runtime image must be pushed to this stack's ECR repository."
    }
  }
  depends_on = [aws_iam_role_policy.runtime, aws_s3_bucket_public_access_block.definitions]
}
