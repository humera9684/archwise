---
title: Traffic spikes, seasonal peaks and cost models
source: https://docs.aws.amazon.com/wellarchitected/latest/framework/cost-optimization.html
tags: traffic spikes seasonal black-friday cost scaling serverless always-on
---
## Short spikes versus sustained load
Per-request and scale-to-zero services are economical when traffic is low or arrives in short bursts, because you pay only for use. When high load lasts for hours or days, an always-on fleet is often cheaper per request. The duration of a spike therefore changes which cost model is cheaper, which is why it is worth asking.

## Preparing for a known event
For a Black Friday or launch, raise service quotas ahead of time, pre-scale or schedule capacity, load test at the expected peak, and make sure downstream dependencies such as the database and third-party APIs can absorb the peak. The weakest component sets the limit.

## Unknown growth
When growth is unpredictable, prefer options that scale without redesign and compare cost across low, medium and high scenarios rather than relying on one forecast.

## Budgets
Usage-based services can exceed a budget silently during a spike. Set budget alerts and, where possible, concurrency or instance caps so that a surge or a bug cannot create an unbounded bill.
