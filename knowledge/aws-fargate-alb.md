---
title: Amazon ECS on AWS Fargate with Application Load Balancer
source: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/
tags: aws managed-containers fargate ecs alb containers
---
## What you manage
Fargate runs containers without managing servers or operating systems. You still own the container image, task sizing, health checks and deployment strategy. It is a middle ground in operations effort: more than serverless functions, far less than Kubernetes.

## Scaling behaviour
ECS service auto scaling adds tasks in response to metrics such as CPU or request count. New tasks take tens of seconds to start, so scaling is slower than function-based services. For predictable events use scheduled scaling or a higher minimum task count.

## Load balancing and availability
HTTP traffic enters through an Application Load Balancer, which is billed hourly plus by capacity units and is a fixed cost even at low traffic. Spreading tasks across at least two Availability Zones keeps the service available if one zone fails.

## Cost shape
Fargate bills per second for vCPU and memory while tasks run. A steady baseline of tasks plus the load balancer gives a floor cost that does not shrink to zero, but it grows more slowly than per-request pricing as traffic rises.
