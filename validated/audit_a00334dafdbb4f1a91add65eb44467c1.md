Based on my investigation, I found one candidate worth flagging but could not fully verify exploit economics or confirm the absence of a minimum-purchase / anti-spam guard before this session ended.

### Title
Permissionless bandwidth sponsorship allows griefing eviction of a victim app's paid, unused bandwidth - (File: `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`pallet-bandwidth` maintains a per-`(app_chain, app)` FIFO list of `Subscription`s capped at 1024 entries [1](#0-0) . Any purchaser on any registered source chain can credit bandwidth to *any* `(app_chain, app)` pair — the pallet keys allowance storage by the `app_chain`/`app` fields taken from the purchase message body, not by the caller's identity, which is explicitly the "sponsorship" feature [2](#0-1) . Pushing onto a full list evicts the oldest entry regardless of remaining unused bytes, emitting `SubscriptionEvicted` [3](#0-2) .

### Finding Description
The report's core broken invariant is: a queue/batch mechanism with a hard cap silently sacrifices honest participants' already-paid-for state when an attacker forces the cap to be reached, and the mechanism that lets the attacker reach the cap is available to an unprivileged party (in the original report: flash supply+withdraw; here: permissionless sponsorship purchase for an arbitrary victim app).

Here, because `purchase()` lets *any* caller credit bandwidth to *any* `(app_chain, app)` pair via the cross-chain sponsorship path [2](#0-1) , and the FIFO subscription list evicts oldest-first once it reaches the 1024 cap [1](#0-0) , an attacker who is not the app owner can push cheap tier purchases into a victim app's queue to force eviction of the victim's older, larger, still-unused subscription. The eviction path does not weigh remaining bytes or discriminate by purchaser — it is a pure FIFO pop on overflow.

### Impact Explanation
If exploitable cheaply (i.e., the victim's list is already near the 1024 cap from legitimate usage, or an attacker is willing to fill remaining slots), this destroys bandwidth the victim already paid real money for, with the loss auditable but irreversible (`SubscriptionEvicted { lost_bytes }`). This is a fund-loss/griefing vector reachable by an unprivileged, non-admin, non-relayer, non-prover actor purely through the public `purchase()` entrypoint — matching the impact-gate criteria (loss of paid-for value via a public function, not requiring a malicious relayer/prover/admin).

### Likelihood Explanation
Likelihood is **uncertain/low-to-moderate** and I could not fully confirm it during this session: eviction only matters once a specific victim's list is at or near the 1024-entry cap, and each attacker purchase costs real money (minimum non-zero tier price) [4](#0-3) . For most apps under normal usage (list far from cap) this attack requires many purchases and is economically unattractive unless the attacker's cost to fill remaining slots is much lower than the value of the bytes evicted from the victim's oldest entry. I was not able to verify within the available budget whether there is any minimum-purchase throttle, per-caller rate limit, or governance safeguard against this specific griefing path, nor whether `set_manager`/registration restricts which source chains can sponsor which apps in practice.

### Recommendation
- Consider restricting eviction so that only the app's own purchases (or admin/force-credit) can trigger eviction of that app's subscriptions, or track "unused bytes value" and refuse to evict a subscription with materially more remaining value than the incoming purchase, unless the cap is raised.
- Alternatively, key the cap/eviction policy per payer, or require the evicting purchase to be for the same or lower-value tier before allowing an evict-on-behalf action from a third party.
- Add telemetry/alerting on `SubscriptionEvicted` events triggered by a `paid_from` different from the app's own historical payers, to detect griefing in production.

### Proof of Concept
Conceptual (not executed against a live/test chain in this session):
1. Victim app `A` on `app_chain` accumulates one legitimate, large-tier subscription with a long remaining `expires_at` and large `remaining_bytes`, sitting near slot 1023 of 1024 in its `SubscriptionList`.
2. Attacker, from any registered source chain, calls `BandwidthManager.purchase()` targeting `app_chain = A`'s chain and `app = A`, paying only for the cheapest configured tier [5](#0-4) .
3. The purchase message credits a new subscription onto `A`'s FIFO list, pushing it to 1024 and evicting the oldest entry (`SubscriptionEvicted`) — potentially A's large unused subscription — for the cost of one cheap-tier purchase paid by the attacker [1](#0-0) .

Given the uncertainty around exploit economics and the possibility of undiscovered mitigations, this should be treated as a candidate requiring further validation (e.g., a Devin session with test-harness access to `modules/pallets/bandwidth`) rather than a confirmed, high-confidence vulnerability.

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L22-30)
```rust
//! Each `(chain, app)` row holds a FIFO list of [`Subscription`]s
//! (`BoundedVec`, capped at 1024). Every purchase appends a new
//! subscription with a fixed `expires_at`; expiry never extends and
//! same-tier repurchases don't stack — they queue. The gate drains
//! the oldest live subscription first; once empty it moves to the
//! next. Subscriptions that aren't reached before their expiry are
//! swept silently — what you paid for is yours only until it expires.
//! Pushes onto a full list evict the oldest entry and emit
//! [`Event::SubscriptionEvicted`].
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L18-27)
```text
### Plans at a glance

| Plan   | Bytes  | $/byte    |
| ------ | ------ | --------- |
| $50    | 100 KB | $0.000500 |
| $100   | 300 KB | $0.000333 |
| $250   | 1 MB   | $0.000250 |
| $1000  | 8 MB   | $0.000125 |

Larger tiers trade upfront commitment for a steep per-byte discount — the $1000 plan is roughly 4× cheaper per byte than the $50 plan.
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L75-77)
```text
### Eviction

Pushing onto a full list (1024 entries) evicts the **oldest** entry and emits `SubscriptionEvicted` with the lost bytes so the loss is auditable on-chain. In practice this only happens under pathological repeat-buy behavior — at the default of one purchase per cycle, 1024 buys is years of headroom.
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L108-114)
```text
## Sponsorship

The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L120-134)
```text
**Setup (one-time, per source chain):**

1. **Deploy.** `BandwidthManager(owner)` is deployed on the source chain.
2. **Bind host.** Owner calls `setHost(hostAddr)` — one-shot.
3. **Register.** Governance calls `pallet-bandwidth::set_manager(source, manager_addr)` on Hyperbridge.
4. **Configure tiers.** Governance sets `(bytes, duration_secs)` on the pallet via `set_tier`, then pushes the price side to the manager via `dispatch_set_tiers`.

Until every step lands, purchases fail — usually `UnknownManager` (pallet) or `UnknownTier()` (manager).

**Purchase (per top-up):**

5. **Approve.** Buyer approves the manager for `tier.price × months` scaled to the local fee token's decimals.
6. **Call `purchase()`.** Manager pulls the fee token, encodes a `BandwidthPurchaseMsg { app, tier, months, chain }`, and dispatches an ISMP POST to `pallet-bandwidth` (recipient `"BWMARKET"`) with `timeout: 0` and `fee: 0`. Emits `BandwidthPurchased` with the dispatch commitment.
7. **Deliver.** A relayer carries the message to Hyperbridge.
8. **Credit.** Pallet's `on_accept` checks `request.from` matches the registered manager, decodes the body, looks up `TierConfig`, computes `bytes × months` and `duration_secs × months`, and appends a fresh `Subscription` to the `(app_chain, app)` FIFO list. Emits `BandwidthCredited { app_chain, app, paid_from, tier, bytes, expires_at }`. If the list was at the 1024 cap, the oldest entry is evicted with `SubscriptionEvicted`.
```
