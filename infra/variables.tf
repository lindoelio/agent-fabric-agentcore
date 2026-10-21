variable "aws_region" {
  type    = string
  default = "us-west-2"
}

variable "project_name" {
  type    = string
  default = "agent-factory"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.project_name))
    error_message = "Use 3-32 lowercase letters, digits or hyphens."
  }
}

variable "runtime_image_uri" {
  description = "Null bootstraps S3/ECR/IAM only. Then set the pushed ECR image URI with @sha256 digest."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition     = var.runtime_image_uri == null ? true : can(regex("^[0-9]{12}\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com(\\.cn)?/.+@sha256:[a-f0-9]{64}$", var.runtime_image_uri))
    error_message = "Use an immutable private ECR image URI with @sha256:<64 hex chars>."
  }
}

variable "definitions_prefix" {
  type    = string
  default = "teams"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]*(/[a-z][a-z0-9-]*)*$", var.definitions_prefix))
    error_message = "Use a normalized lowercase S3 prefix without a trailing slash."
  }
}

variable "session_storage_enabled" {
  description = "Managed session storage is a preview feature. It expires and resets on runtime version updates; see README."
  type        = bool
  default     = true
}

variable "idle_timeout_seconds" {
  type    = number
  default = 900
  validation {
    condition     = var.idle_timeout_seconds >= 60 && var.idle_timeout_seconds <= 28800
    error_message = "Idle timeout must be between 60 and 28800 seconds."
  }
}

variable "max_lifetime_seconds" {
  type    = number
  default = 28800
  validation {
    condition     = var.max_lifetime_seconds >= 60 && var.max_lifetime_seconds <= 28800
    error_message = "microVM lifetime must be between 60 and 28800 seconds."
  }
}

variable "bedrock_model_ids" {
  description = "Foundation model IDs and matching US inference-profile IDs; configure both when adding models."
  type        = set(string)
  default     = ["anthropic.claude-sonnet-4-5-20250929-v1:0"]
  validation {
    condition     = length(var.bedrock_model_ids) > 0 && alltrue([for id in var.bedrock_model_ids : !strcontains(id, "*")])
    error_message = "Specify model IDs explicitly without wildcards."
  }
}

variable "inference_profile_ids" {
  type    = set(string)
  default = ["us.anthropic.claude-sonnet-4-5-20250929-v1:0"]
  validation {
    condition     = alltrue([for id in var.inference_profile_ids : !strcontains(id, "*")])
    error_message = "Specify inference-profile IDs without wildcards."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
