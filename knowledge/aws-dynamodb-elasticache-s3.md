---
title: Amazon DynamoDB, ElastiCache and S3
source: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/
tags: aws key-value dynamodb elasticache redis cache s3 storage cloudfront cdn
---
## DynamoDB
DynamoDB is a managed key-value and document database with on-demand capacity that absorbs spikes without provisioning. Standard tables carry a 99.99% availability commitment and global tables 99.999%. Access patterns must be designed up front around keys; ad hoc queries and joins are not supported the way they are in PostgreSQL.

## ElastiCache for Redis
A cache lowers database load and latency, but adds a component to operate and a consistency problem: cached data can be stale until it expires or is invalidated. Enable Multi-AZ with automatic failover for availability, and design for a cold cache after failover.

## S3 and CloudFront
S3 stores objects such as product images and backups durably and cheaply. Putting CloudFront in front lowers latency for distant users and reduces data transfer from the origin, which matters for media-heavy applications with global users.
