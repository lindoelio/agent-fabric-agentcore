# Working on this repository

This project hosts unmodified dcode in AgentCore Runtime. Do not substitute the
Deep Agents SDK backend silently: the MVP intentionally preserves dcode semantics.

- Keep dependencies minimal. Core release loading and protocol tests use stdlib.
- Pin dcode and validate its CLI/ACP contract before upgrading.
- Never run Terraform apply, publish images or definitions, or call paid models as
  part of ordinary tests. Those require explicit deployment instructions.
- Never commit credentials, tfvars, Terraform state, workspaces or session profiles.
- Run `PYTHONPATH=src python -m unittest discover -s tests -v` for relevant changes.
- Run `terraform -chdir=infra fmt -check` and `validate` when Terraform is available.
- Preserve the one-team-version-per-runtime-session binding and one-worker model.
- Document any gap between test fixtures, real dcode and actual AWS validation.
