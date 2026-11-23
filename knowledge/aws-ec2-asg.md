---
title: Amazon EC2 Auto Scaling with a load balancer
source: https://docs.aws.amazon.com/autoscaling/ec2/userguide/
tags: aws virtual-machines ec2 auto-scaling alb
---
## What you manage
You own the operating system, patching, images, deployment and capacity planning. That is the most hands-on pattern for a small team, but it gives the lowest price per request at steady, predictable load and full control over the runtime.

## Scaling and availability
An Auto Scaling group replaces failed instances and can span Availability Zones behind a load balancer. Scale-out takes minutes because instances must boot and pass health checks, so keep headroom for sudden spikes or use scheduled scaling for known events.

## Cost levers
Instances are billed per second. Savings Plans or reserved capacity lower the steady-state price substantially, but ArchWise prices on-demand rates only, so real costs for always-on fleets can be lower than shown.

## Risk
Patching and image hygiene are your responsibility. Without automation and monitoring, drift and unpatched hosts become the main operational and security risk.
