variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name prefix for every resource"
  type        = string
  default     = "yatra-pulse"
}

variable "kubernetes_version" {
  description = "EKS Kubernetes version (standard support)"
  type        = string
  default     = "1.35"
}

variable "node_instance_type" {
  description = "Worker node size. Knative + Pulsar + the app need about 6 GiB across the nodes."
  type        = string
  default     = "t3.large"
}

variable "node_count" {
  description = "Number of worker nodes"
  type        = number
  default     = 2
}

variable "gateway_node_port" {
  description = "NodePort of the in-cluster edge gateway that the ALB targets"
  type        = number
  default     = 30080
}

variable "enable_cloudfront" {
  description = "Put CloudFront in front of the ALB. Needs an AWS account verified for CloudFront; when false, the ALB is public on HTTP."
  type        = bool
  default     = true
}
