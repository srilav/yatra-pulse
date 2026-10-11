# Idle-cost control: scale the EKS node group to zero overnight and back in the morning with
# EventBridge Scheduler calling the EKS API (no Lambda, no extra compute).
# Knative already scales the app pods to zero; this stops the EC2 nodes themselves.
# Still billed while nodes are off: EKS control plane, NAT gateway, ALB (~$0.18/h). Only teardown stops those.
# Manual control: deploy/aws/nodes.sh stop|start|status

variable "idle_schedule" {
  description = "Stop/start the worker nodes on a schedule (cron in the given time zone)."
  type = object({
    enabled    = bool
    timezone   = string
    stop_cron  = string
    start_cron = string
  })
  default = {
    enabled    = true
    timezone   = "Asia/Kolkata"
    stop_cron  = "cron(0 22 * * ? *)" # 22:00 every day
    start_cron = "cron(0 7 * * ? *)"  # 07:00 every day
  }
}

locals {
  node_group      = module.eks.eks_managed_node_groups["default"]
  node_group_name = split(":", local.node_group.node_group_id)[1]
}

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

data "aws_caller_identity" "current" {}

resource "aws_iam_role" "node_scheduler" {
  count              = var.idle_schedule.enabled ? 1 : 0
  name               = "${var.name}-node-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

resource "aws_iam_role_policy" "node_scheduler" {
  count = var.idle_schedule.enabled ? 1 : 0
  name  = "update-nodegroup-size"
  role  = aws_iam_role.node_scheduler[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["eks:UpdateNodegroupConfig", "eks:DescribeNodegroup"]
      Resource = local.node_group.node_group_arn
    }]
  })
}

resource "aws_scheduler_schedule" "nodes_stop" {
  count                        = var.idle_schedule.enabled ? 1 : 0
  name                         = "${var.name}-nodes-stop"
  description                  = "Scale Yatra Pulse EKS nodes to 0 (idle hours)"
  schedule_expression          = var.idle_schedule.stop_cron
  schedule_expression_timezone = var.idle_schedule.timezone
  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:eks:updateNodegroupConfig"
    role_arn = aws_iam_role.node_scheduler[0].arn
    input = jsonencode({
      ClusterName   = module.eks.cluster_name
      NodegroupName = local.node_group_name
      ScalingConfig = { MinSize = 0, DesiredSize = 0, MaxSize = var.node_count + 1 }
    })
    retry_policy { maximum_retry_attempts = 3 }
  }
}

resource "aws_scheduler_schedule" "nodes_start" {
  count                        = var.idle_schedule.enabled ? 1 : 0
  name                         = "${var.name}-nodes-start"
  description                  = "Scale Yatra Pulse EKS nodes back up (working hours)"
  schedule_expression          = var.idle_schedule.start_cron
  schedule_expression_timezone = var.idle_schedule.timezone
  flexible_time_window { mode = "OFF" }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:eks:updateNodegroupConfig"
    role_arn = aws_iam_role.node_scheduler[0].arn
    input = jsonencode({
      ClusterName   = module.eks.cluster_name
      NodegroupName = local.node_group_name
      ScalingConfig = { MinSize = var.node_count, DesiredSize = var.node_count, MaxSize = var.node_count + 1 }
    })
    retry_policy { maximum_retry_attempts = 3 }
  }
}

output "node_group_name" { value = local.node_group_name }
output "idle_schedule" {
  value = var.idle_schedule.enabled ? "nodes stop ${var.idle_schedule.stop_cron} and start ${var.idle_schedule.start_cron} (${var.idle_schedule.timezone})" : "disabled"
}
