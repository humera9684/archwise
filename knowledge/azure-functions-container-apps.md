---
title: Azure Functions Flex Consumption and Azure Container Apps
source: https://learn.microsoft.com/azure/azure-functions/
tags: azure serverless managed-containers functions container-apps flex-consumption
---
## Azure Functions Flex Consumption
Flex Consumption scales out quickly, can scale to zero, and lets you configure always-ready instances to avoid cold starts. HTTP-triggered functions are subject to a 230-second idle timeout at the front-end load balancer, so long operations should be asynchronous.

## Azure Container Apps
Container Apps runs containers on a managed Kubernetes-based environment without exposing Kubernetes. Event-driven autoscaling can scale to zero, and a dedicated plan provides steady capacity. It is a good fit for web apps that want containers with low operational overhead.

## Spikes and quotas
Both services have regional and per-app instance limits. Request quota or limit increases ahead of a known traffic event and load test the scaling profile.

## Database connections
As with other serverless compute, many concurrent instances can exhaust PostgreSQL connections. Use a connection pooler such as the built-in PgBouncer in Azure Database for PostgreSQL Flexible Server.
