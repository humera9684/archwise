---
title: Amazon EKS
source: https://docs.aws.amazon.com/eks/latest/userguide/
tags: aws kubernetes eks
---
## Operational load
EKS runs the Kubernetes control plane for you; you still operate worker nodes or node groups, networking add-ons, ingress, observability, upgrades and access control. A team needs real Kubernetes experience. This is the highest operations overhead of the four patterns.

## Fixed costs
Each cluster carries a flat hourly control-plane fee in addition to worker compute and a load balancer. For small workloads this fixed cost dominates, which makes Kubernetes a poor fit unless you need its portability or ecosystem.

## Upgrades
Kubernetes versions have a limited support window. Clusters must be upgraded on a regular schedule or they move to paid extended support, so budget engineering time for upgrades.

## When it fits
Choose Kubernetes when you already run many services, need portability across clouds, or rely on its tooling. For a single web application with a small team, managed containers or serverless are usually the better trade.
