---
title: Designing for availability and multiple regions
source: https://docs.aws.amazon.com/wellarchitected/latest/framework/
tags: availability sla multi-region reliability failover global europe
---
## Composite availability
The availability of a request path is the product of the availability of every service it depends on. Each additional dependency lowers the total, which is why a design with a database, cache and gateway in series can fall below the target even when each part is individually strong. Redundancy across zones raises the result; a second Region raises it further.

## Multi-Region costs and complexity
Running in two Regions roughly doubles infrastructure cost and adds data replication, consistency and conflict handling. Active-active needs globally replicated data and global traffic routing; active-passive is cheaper but has a failover time. Untested failover does not provide the availability you are paying for, so rehearse it.

## Users on several continents
Latency between continents is dominated by distance, so a single-Region design adds a round trip of roughly a hundred milliseconds or more for distant users. A CDN helps static and cacheable content; dynamic requests need either a nearby Region or tolerance for the extra latency.

## Cost of each extra nine
Moving from 99.9% to 99.99% allowed downtime drops from tens of minutes a month to a few minutes. That usually requires zone redundancy for every tier and often a second Region, so confirm the business need before accepting the cost.
