# Graph analysis

The bus graph has two calculations over the same authorized traffic snapshot:
normalized-Laplacian coordinates for placement, and Leiden community detection
for grouping. The browser performs both locally. No messages are sent, no extra
network endpoint is called, and no message contents are required.

## What the graph measures

Each node is an immutable registered agent ID. Names, owners, and devices are
display attributes; duplicate names never merge agents. Each directed edge
counts retained broker messages between two agents currently visible on the
selected bus. This includes queued and unsuccessful delivery records, so traffic
volume does not establish that an agent completed work.

Analysis combines the two directions into one undirected weight:

```
w(i,j) = log(1 + count(i → j) + count(j → i))
w(i,i) = 0
```

The logarithm compresses differences in volume. The arrows and inspector retain
the actual directed counts; the layout does not substitute transformed weights
for message counts. Self-messages do not determine placement or communities.

The input is retained activity, not a complete time series. Receipt retention
starts at a terminal receipt's latest update. Local socket/SSH traffic that
bypasses the broker is absent. Filtering the view changes the graph being
analyzed. No hidden endpoint or out-of-view bus contributes to its matrix.

## Spectral placement

For each connected component with at least two agents, let `W` be its symmetric
weight matrix and `D` the diagonal matrix of weighted degrees. Compute:

```
L = I − D^(−1/2) W D^(−1/2)
```

The two lowest nonzero eigenmodes supply the two coordinates. The zero mode is
proportional to the square root of degree. A
two-agent component has one nonzero mode and uses a line. Isolated agents
are placed separately. Components are packed independently; the distance between
disconnected components has no inferred relationship meaning.

These modes capture low variation across strong links after degree normalization.
They are not physical coordinates, a timeline, reporting lines, or an exact
pairwise distance scale. Structurally similar agents can receive nearly
identical coordinates. A separate card-spacing pass prevents overlapping cards;
the Analysis panel reports the underlying spectrum before that display
correction.

Node ordering and eigenspace orientation are deterministic. Repeated eigenvalues
need a canonical basis as well as a sign convention. The layout avoids using an
arbitrary solver orientation as a meaningful change in the network.

Background: [Koren, *Drawing Graphs by Eigenvectors: Theory and Practice*](https://www.math.ucdavis.edu/~saito/data/acha.read.w12/koren-graph-drawing.pdf).

## Communities

Leiden uses the same undirected weights to optimize weighted modularity at
resolution one. With `k(i)` the weighted degree and `2m` the sum of all degrees:

```
Q = (1 / 2m) Σ(i,j) [w(i,j) − k(i)k(j)/(2m)] · same_group(i,j)
```

It compares observed internal weight with the degree-based null model. The
number of communities is inferred rather than supplied as a fixed team count.
Canonical input order and seeded randomness make identical inputs reproducible.
Disconnected and isolated agents do not acquire invented links.

Group labels are neutral identifiers. The partition is a heuristic at this
resolution, not a unique truth about the organization or evidence of common
research topics. Weighted modularity is the objective value for this graph,
not an agent performance score. A graph without edges has no modularity value.

Collapsing a group aggregates its current members' directed external traffic;
internal traffic remains a count on the group. Expanding restores the individual
agents. Collapsing, selecting, or recoloring never changes bus grants, agent
registration, or routing.

Background: [Traag, Waltman, and Van Eck, *From Louvain to Leiden*](https://doi.org/10.1038/s41598-019-41695-z).
Implementation: [networkanalysis-ts](https://github.com/neesjanvaneck/networkanalysis-ts),
the authors' TypeScript port, with a locked dependency version.

## Stability and access

Ordinary updates retain existing node positions. New traffic can be applied
with **Recompute**; pins preserve deliberately placed agents. The interface
identifies analysis based on an earlier traffic snapshot. Opening a panel does
not resize the canvas.

Removal is immediate: revoked or filtered-out identities, memberships, and
relationships are removed from nodes, community members, aggregates, and
analysis. Scope changes and sign-out clear all view state. No private layout
cache persists across a lost authorization boundary.

Dense spectral work is bounded. When a view exceeds the supported analysis
limit, all agents remain available in a fallback arrangement with an explicit
notice; narrowing the view enables analysis. A fallback is never labeled as a
successful spectral result. The exact bound and numerical tests are documented
in the [graph package](../packages/bus-graph/README.md).
