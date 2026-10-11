# EKS cluster with one managed node group in the private subnets.
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "21.28.0"

  name               = var.name
  kubernetes_version = var.kubernetes_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  endpoint_public_access = true

  # No customer-managed KMS key: a deleted key lingers for 7+ days pending deletion, which would
  # stop teardown from leaving a clean account. EKS still encrypts etcd at rest by default.
  create_kms_key                           = false
  encryption_config                        = null
  enable_cluster_creator_admin_permissions = true

  addons = {
    coredns                = {}
    kube-proxy             = {}
    vpc-cni                = { before_compute = true }
    eks-pod-identity-agent = { before_compute = true }
    aws-ebs-csi-driver = {
      pod_identity_association = [{
        role_arn        = aws_iam_role.ebs_csi.arn
        service_account = "ebs-csi-controller-sa"
      }]
    }
  }

  eks_managed_node_groups = {
    default = {
      instance_types = [var.node_instance_type]
      min_size       = var.node_count
      max_size       = var.node_count + 1
      desired_size   = var.node_count
      disk_size      = 30
    }
  }

  # Let the ALB reach the edge gateway's NodePort on the nodes.
  node_security_group_additional_rules = {
    alb_to_gateway = {
      description              = "ALB to edge gateway NodePort"
      protocol                 = "tcp"
      from_port                = var.gateway_node_port
      to_port                  = var.gateway_node_port
      type                     = "ingress"
      source_security_group_id = aws_security_group.alb.id
    }
  }
}

# EBS CSI driver identity (Pulsar's persistent volume), via EKS Pod Identity.
data "aws_iam_policy_document" "pod_identity_trust" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ebs_csi" {
  name               = "${var.name}-ebs-csi"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}
