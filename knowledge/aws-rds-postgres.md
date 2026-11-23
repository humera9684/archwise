---
title: Amazon RDS for PostgreSQL
source: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/
tags: aws relational database rds postgresql multi-az
---
## High availability
A Multi-AZ deployment keeps a synchronous standby in another Availability Zone and fails over automatically, typically within a couple of minutes. Multi-AZ carries a higher availability commitment than a single-AZ instance and roughly doubles the instance price.

## Read replicas are not high availability
Read replicas replicate asynchronously and exist to offload reads. They do not replace Multi-AZ, and a cross-Region replica must be promoted manually, which means a multi-Region design has a recovery step unless you automate it.

## Connection limits
The maximum number of connections depends on instance memory. Serverless and highly parallel compute can exhaust it quickly; use RDS Proxy or an application-side pool. This is the most common failure mode when a spike hits a serverless front end.

## Scaling
Relational databases scale up (bigger instance) far more easily than out. Writes concentrate on one primary, so a write-heavy workload with very high peak queries per second may outgrow a single instance and push the design toward a key-value store or sharding.
