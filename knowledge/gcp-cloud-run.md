---
title: Google Cloud Run
source: https://cloud.google.com/run/docs
tags: gcp serverless managed-containers cloud-run containers
---
## Two billing modes
With request-based billing an instance is charged only while it handles requests and can scale to zero. With instance-based billing CPU stays allocated for the whole instance lifetime, which suits steady traffic and background work and is cheaper per request at sustained load.

## Concurrency and scaling
One instance serves many requests at once (80 by default, configurable up to 1,000), so Cloud Run often needs fewer instances than a one-request-per-environment model. Setting a minimum number of instances removes most cold starts at the cost of paying for idle capacity.

## Limits
A request can run for up to 60 minutes. Containers must be stateless and listen on the port provided. Scale is bounded by a configurable maximum instance count and regional quotas, so raise limits before a known event.

## Availability and databases
Cloud Run is regional and runs across zones in that region. Connect to Cloud SQL through the Cloud SQL connector and cap the maximum instances so that instance count times pool size stays under the database's connection limit.
