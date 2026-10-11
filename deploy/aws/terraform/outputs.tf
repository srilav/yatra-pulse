output "region" { value = var.region }
output "cluster_name" { value = module.eks.cluster_name }
output "ecr_registry" { value = split("/", aws_ecr_repository.app["web"].repository_url)[0] }
output "ecr_repositories" { value = { for k, r in aws_ecr_repository.app : k => r.repository_url } }
output "alb_dns_name" { value = aws_lb.edge.dns_name }
output "cloudfront_url" { value = "https://${aws_cloudfront_distribution.edge.domain_name}" }
output "cloudfront_distribution_id" { value = aws_cloudfront_distribution.edge.id }
output "gateway_node_port" { value = var.gateway_node_port }
