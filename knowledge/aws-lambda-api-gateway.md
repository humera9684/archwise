---
title: AWS Lambda and API Gateway
source: https://docs.aws.amazon.com/lambda/latest/dg/
tags: aws serverless lambda api-gateway functions
---
## Scaling and concurrency
Lambda scales by running one execution environment per concurrent request. New accounts have a default regional concurrency quota of 1,000 concurrent executions, shared by all functions in the Region. A sudden spike above the quota is throttled, so raise the quota before expected events such as a sale or launch.

## Cold starts
The first request to a new execution environment pays an initialization delay. Provisioned concurrency keeps environments warm and removes most cold starts, at an hourly charge. Larger packages, VPC-attached functions and heavier runtimes start more slowly.

## Limits to design around
A function can run for at most 15 minutes. Synchronous requests through API Gateway have a much shorter integration timeout, 29 seconds by default, so long jobs belong in a queue. Memory is configurable and CPU share scales with it, so a memory-hungry or CPU-bound workload gets more CPU only by buying more memory.

## Risk: database connections
Every concurrent Lambda execution opens its own database connection. A traffic spike can exhaust a relational database's connection limit long before CPU is a problem. Put RDS Proxy (or another pooler) between Lambda and the database, or use a database that is accessed over HTTP such as DynamoDB.

## Cost shape
Billing is per request plus per GB-second of execution time. Cost is near zero at idle and grows linearly with traffic, so at sustained high load an always-on fleet is usually cheaper per request.
