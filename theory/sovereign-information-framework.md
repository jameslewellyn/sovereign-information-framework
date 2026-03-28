# Sovereign Information Framework — Theory

> A personal intelligence system that treats all external information as adversarial by default, builds a quantified model of item economics, and resolves truth through a personalized, decentralized epistemology engine.

---

## The Core Problem

Modern information environments are optimized for engagement and extraction, not for the user's benefit. Ads, product listings, social posts, news, and recommendations are all produced by parties with interests that may conflict with yours. Prices obscure unit economics. Social signals obscure source distance and bias. Truth claims arrive without provenance.

The Sovereign Information Framework (SIF) is a personal infrastructure layer that inverts this: **you define the trust model, the weights, and the decision logic. The framework enforces it.**

---

## Two Primary Modules

### 1. Personal Economics Engine
Models the full lifecycle cost and value of physical items you own or may acquire.

### 2. Information Trust Layer
Scores, filters, and routes all incoming information through a personalized adversarial trust model before it reaches you.

These modules share a common substrate: a decentralized, append-only debate graph that provides the epistemic backbone for both.

---

## Module 1 — Personal Economics Engine

### Item-Level Economics

Every item in the system carries a rich economic profile:

| Dimension | Description |
|---|---|
| Price history | Time-series of observed prices across sources |
| Unit price | Normalized to canonical base unit (oz, roll, load, count, etc.) |
| Pack size | Structured representation enabling cross-size comparison |
| Amount on hand | Current inventory count |
| Consumption rate | Historical rate of use (units/day or units/week) |
| Reorder point | Derived from consumption rate + lead time |
| Cost to acquire | Price + delivery + taxes + time cost at user's hourly rate |
| Storage capacity | Physical constraint on max stock |
| Special storage instructions | Temperature, humidity, light, containment requirements |
| Special disposal instructions | Hazmat, recycling, medication disposal, e-waste, etc. |
| Depreciation | Rate of value loss over time (durables, electronics, tools) |
| Appreciation | Rate of value gain (collectibles, certain foods, materials) |
| Scarcity | Supply signal — local, regional, or global availability |
| Brand quality | Normalized quality score from aggregated sources |
| Brand identity | Social/ethical stance flags (labor, environment, politics) |
| Rewards programs | Cashback, points, fuel discounts as real price offsets |
| Rate of production | How fast the item is manufactured / restocked |
| Urgency | Time pressure on acquisition decision |
| Upcoming sales | Known future price drops from flyers or historical patterns |

### Household-Level Economics (Accounting Layer)

The household is modeled as a small entity with a balance sheet:

- **Inventory valuation** — FIFO, LIFO, or weighted average cost selectable per category
- **Net inventory value** — aggregate on-hand stock valued at acquisition cost
- **Monthly consumption spend** — actual cost of goods used, not purchased
- **Shrinkage** — spoilage, expiry waste, loss tracked as a cost center
- **Cash tied up in stock** — opportunity cost of over-buying
- **Accrual mapping** — purchases allocated to the month they will be consumed
- **Household inflation rate** — your personal CPI derived from your actual consumption basket, compared to reported CPI

### Micro and Macro Economic Layers

**Micro (item-level signals):**
- Supply/demand elasticity per item (does price swing seasonally or stay flat?)
- Substitution graph (if item A is expensive, what are ranked alternatives?)
- Marginal value of buying one more unit now vs. waiting
- Local market power (warehouse clubs as price setters vs. grocery as price takers)

**Macro (household and external signals):**
- Inflation eroding purchasing power over time
- CPI category mapping to your actual basket
- Supply chain disruption signals affecting availability
- Regional cost-of-living deltas
- Interest rate environment affecting "stock up now vs. defer" calculus

### Time Horizons

The engine operates across three simultaneously:

- **Short** — what to buy this week: active deals, stock levels, urgency signals, upcoming sales
- **Medium** — monthly optimization: budget tracking, consumption rates, reorder planning, waste reduction
- **Long** — annual trends: cost-of-living trajectory, inflation exposure, net inventory value over time, brand/quality drift

---

## Module 2 — Information Trust Layer

### Foundational Assumption

All public information is treated as an **untrusted broadcast**. Social media, ads, reviews, news, product listings, and recommendations are produced by parties with incentives that may not align with yours. The default trust score for any incoming data is zero until earned.

### Information Flow

```
Raw Stream (social, ads, news, prices, claims)
    ↓
Source Adapter (normalizes to standard record)
    ↓
Tag / Topic Subscription Filter
    ↓
Filter Services (subscribed curators, algorithms, people)
    ↓
Confidence Scoring Engine
    ↓
Decider Network Aggregation
    ↓
Personalized Pre-Answer / Recommendation
    ↓
User Interface (CLI, dashboard, agent, digest)
```

### Confidence Scoring

Every piece of information entering the system receives a quantitative confidence score derived from:

1. **Source distance (hops)** — your own signal > trusted contact > contact-of-contact > public stranger > anonymous. Each hop degrades the base confidence by a configurable decay factor.
2. **Filter service votes** — how many of your subscribed filter services flagged, endorsed, or ignored this item, weighted by your trust in each filter.
3. **Decider network position** — what your trusted deciders have said about the underlying claim (via the debate graph).
4. **Historical accuracy** — how often this source's past signals were validated by later evidence.
5. **Adversarial pattern flags** — known manipulation patterns (fake urgency, astroturfing, coordinated inauthentic behavior, dark patterns).

### Zero Trust Architecture

- No source is implicitly trusted — every source must earn confidence through scoring.
- **Data segmentation**: social signals, commercial signals, and personal data are isolated in separate trust domains. A high trust score in one domain does not transfer to another.
- **Threat modeling per source type**: ads carry a commercial adversarial model; social posts carry a social influence adversarial model; news carries a narrative framing model. Each has its own scoring heuristics.
- **Data minimization**: the system requests only what it needs from each source and does not allow cross-domain data leakage.

---

## The Epistemic Backbone — Decentralized Debate Graph

### Core Principle

Nothing is universally true or settled. The system does not seek global consensus. Instead, it builds a **personalized, weighted truth view** for each user from their decider network.

### Structure

- **Questions and answers are on-chain** — append-only, immutable, publicly citable.
- **Answers are composable** — atomic datapoints can be cited by and rolled up into larger answers, forming a proof/citation graph.
- **Permanent disagreement is the default** — the system never marks a question as closed.

### Deciders

A decider is any entity that can emit a weighted position on a claim. The decider model is fully modular:

| Decider Type | Examples |
|---|---|
| People in your social graph | Friends, experts you follow |
| Institutions | Specific journals, labs, NGOs, standards bodies |
| AI models | Models with characterized known biases and domains |
| Algorithmic filters | Subscribed curation services |
| Your past self | Your own historical stances and overrides |

### Truth Resolution for a User

1. A claim arrives (e.g. "this product is ethically sourced", "this sale price is genuine").
2. The system queries the debate graph for positions on that claim.
3. Positions from your decider network are retrieved, each weighted by your assigned trust to that decider.
4. An **aggregate pre-answer** is computed — a probability-weighted view personalized to your network.
5. The pre-answer is presented alongside the contributing deciders and their positions.
6. **You can override** — your explicit stance + explanation is recorded as a new on-chain datapoint, which can itself be cited by others in your network.

### Connection to the Economics Engine

- Product claims ("this brand uses ethical labor") are resolved through the debate graph before contributing to the brand identity score.
- Price claims ("this is a genuine sale, not inflated baseline") are scored adversarially before being recorded as price observations.
- Review signals are weighted by the reviewer's social distance and decider trust before influencing quality scores.

---

## Architectural Principles

1. **Modular source adapters** — any data input (scraper, API, manual entry, receipt scan, bank feed, sensor) implements a standard ingestion contract.
2. **Modular deciders** — any truth source implements a standard position-emitting interface.
3. **Modular output interfaces** — the reasoning engine produces structured responses; CLI, web, agent, email, and mobile are presentation plugins.
4. **Interruptible and resumable** — all operations are stateful and can be paused and resumed; AI agents and humans use the same function surface.
5. **Local-first** — personal data, weights, and stances live on your hardware by default. External systems receive only what you explicitly share.
6. **Append-only history** — price observations, trust scores, stances, and debate positions are never deleted, only superseded.
7. **Human override always wins** — the system's aggregated outputs are recommendations, not decisions. The user's explicit stance always takes precedence and is recorded.

---

## Decision Outputs (What the System Tells You)

### Short-term
- "Buy X now — lowest unit price in 90 days, 3 weeks of stock remaining"
- "Wait on Y — sale starts Thursday, 18% cheaper per unit"
- "This product review scores 0.31 confidence — 2 hops from you, no decider coverage"

### Medium-term
- "You have 6 weeks of paper towels — skip this week's run"
- "Brand A's ethical sourcing claim scores 0.62 in your network — 3 deciders disagree"
- "Monthly consumption spend is $340, up 8% from last month"

### Long-term
- "Your household inflation rate is 4.8% vs reported CPI 3.1% — driven by dairy and cleaning supplies"
- "Net inventory value: $1,240 — $180 at risk of expiry in 60 days"
- "This information source has been adversarially flagged 14 times in 6 months by your filter network"

---

## What This Is Not

- Not a social network — it consumes public social data but does not publish on your behalf.
- Not a consensus engine — it does not seek or enforce universal truth.
- Not a recommendation algorithm optimized for engagement — it is optimized for your declared values and weights.
- Not a surveillance tool — personal data and stances are local-first and never shared without explicit action.
