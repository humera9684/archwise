---
title: Azure Kubernetes Service and Virtual Machine Scale Sets
source: https://learn.microsoft.com/azure/aks/
tags: azure kubernetes aks virtual-machines vmss application-gateway
---
## AKS tiers
The Standard tier provides a financially backed uptime SLA for the control plane, higher when the cluster uses availability zones. The Free tier has no financially backed SLA and suits experiments, not production. You still operate node pools, upgrades, ingress and observability.

## Virtual Machine Scale Sets
Scale sets autoscale identical VMs across zones behind an Application Gateway. You own patching and images. This pattern gives the lowest steady-state cost and the highest operational responsibility.

## Application Gateway
Application Gateway v2 is billed by the hour plus capacity units, a fixed cost even at low traffic. Run at least two instances across zones for its availability commitment.
