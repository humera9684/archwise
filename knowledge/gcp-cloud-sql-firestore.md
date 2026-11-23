---
title: Google Cloud SQL for PostgreSQL and Firestore
source: https://cloud.google.com/sql/docs
tags: gcp relational database cloud-sql postgresql firestore key-value
---
## Cloud SQL high availability
The high-availability configuration places a standby in a second zone and fails over automatically, with a brief interruption. It carries a higher availability commitment than a single-zone instance and roughly doubles instance cost. Read replicas offload reads but are not a substitute for high availability.

## Connections and scaling
Cloud SQL limits concurrent connections by instance size. Pool connections from serverless services and cap maximum instances. Scale vertically; writes go to a single primary.

## Firestore
Firestore is a serverless document database that scales automatically. Multi-region databases carry a higher availability commitment than regional ones, with a price premium. Queries must match available indexes, and heavy relational reporting is a poor fit.
