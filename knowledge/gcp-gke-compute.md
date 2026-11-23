---
title: Google Kubernetes Engine Autopilot and Compute Engine
source: https://cloud.google.com/kubernetes-engine/docs/concepts/autopilot-overview
tags: gcp kubernetes gke autopilot virtual-machines compute-engine
---
## GKE Autopilot
Autopilot manages nodes for you and bills for the resources your pods request, which removes node sizing and patching. You still operate Kubernetes workloads, ingress and upgrades. A cluster management fee applies per cluster. Regional clusters have a higher control-plane availability commitment than zonal clusters.

## Compute Engine managed instance groups
A managed instance group autoscales and heals VMs behind a Cloud Load Balancer, and can be regional across zones. You own OS patching and images. Sustained-use and committed-use discounts lower steady-state cost, but ArchWise uses on-demand prices only.

## Choosing
Pick Autopilot when you want Kubernetes with less node management, and managed instance groups when you want the lowest steady cost and accept the operational work.
