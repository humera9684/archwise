---
title: Azure Database for PostgreSQL Flexible Server and Azure Cosmos DB
source: https://learn.microsoft.com/azure/postgresql/
tags: azure relational database postgresql flexible-server cosmos-db key-value
---
## PostgreSQL Flexible Server high availability
Zone-redundant high availability places the standby in a different availability zone and has a higher availability commitment than same-zone high availability, which in turn exceeds a server with no high availability. Higher availability roughly doubles compute cost. Built-in PgBouncer helps with connection spikes.

## Scaling
Scale compute and storage vertically; there is a single writer. Very high write rates may need a different data model.

## Cosmos DB
Cosmos DB is a globally distributed multi-model database with serverless and provisioned throughput modes. Availability commitments rise with multi-region configurations. Costs depend on request units, so poorly modelled queries and partition keys can become expensive quickly.
