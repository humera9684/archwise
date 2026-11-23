---
title: Latency, cold starts and caching
source: https://docs.aws.amazon.com/wellarchitected/latest/framework/performance-efficiency.html
tags: latency performance cold-start caching p95
---
## What drives p95 latency
Tail latency is the sum of compute time, database round trips, load-balancer or gateway overhead, network distance and any cold start. Database round trips are often the largest and the easiest to reduce with caching or fewer queries per request.

## Cold starts
Serverless platforms start new execution environments on demand. Keep minimum or provisioned capacity for latency-sensitive paths and avoid heavyweight initialization. Containers that stay warm avoid the problem but cost a steady baseline.

## Caching
A cache in front of the database cuts read latency and database load. It adds an operational component and a staleness risk, so decide which data can tolerate it and how it is invalidated.

## Estimates versus measurements
Analytical latency estimates are useful for ruling options in or out, but only a load test of the real application confirms a latency target.
