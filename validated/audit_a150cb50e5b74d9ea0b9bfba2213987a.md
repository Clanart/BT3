## Analysis

The external report's core broken invariant: a hardcoded numeric cap (`batchesAmount ≤ 100`) was set to a value inconsistent with the economic parameters of the system (5B tokens ÷ 5M/batch = 1000 batches needed), permanently under-serving/mis-serving legitimate value distribution because nobody validated the cap against the real accounting.

I traced this pattern through Hyperbridge's caps and bounded structures (`MAX_STATE_MACHINE_COMMITMENTS`, `MAX_CONSENSUS_MESSAGES_PER_TX`, `MAX_MARKUP_BPS`, the intents relayer reward idempotency map, and `pallet-bandwidth`'s `SubscriptionList` capped at `MAX_SUBSCRIPTIONS = 1024`) looking for a case where an unprivileged caller can exploit the cap boundary to cause loss of funds for a rightful beneficiary — the closest structural analog to the report.

`pallet-bandwidth`'s FIFO `SubscriptionList` is capped at 1024 entries, and pushing onto a full list **evicts the oldest entry** and emits `SubscriptionEvicted`, permanently destroying the bytes the evicted purchaser already paid for: [1](#0-0) [2](#0-1) 

Critically, `purchase()` on `BandwidthManager.sol` is a public, unprivileged entrypoint that lets **any caller** credit **any arbitrary `app` on any `chain`** — it is not restricted to the app owner: [3](#0-2) [4](#0-3) 

Because credits are keyed by `(app_chain, app)` taken from the message body — not by the payer's identity — this is explicitly designed to allow third-party sponsorship: [5](#0-4) 

However, this same openness means an attacker can grief a victim: after a victim legitimately buys a large/expensive tier subscription for their app, an attacker repeatedly calls `purchase()` targeting that same victim `(chain, app)` pair with the cheapest available tier, in a tight loop, until the FIFO list fills to 1024 and starts evicting from the head — pushing out the victim's paid-for, unexpired subscription. `SubscriptionEvicted` is emitted with `lost_bytes`, confirming this is a real, on-chain-acknowledged fund-loss event: [6](#0-5) 

I was unable to fully verify within the remaining budget (a) the exact `MAX_SUBSCRIPTIONS` constant value and eviction code path in `types.rs`/`lib.rs::on_accept`, (b) whether `BandwidthManager.purchase()` enforces any per-call minimum spend or rate limit that would make the 1024-purchase griefing economically infeasible, and (c) whether governance's `force_credit` or the FIFO drain order provides any additional protection against this eviction path for a specific victim's higher-value entry. These would need direct reading of `modules/pallets/bandwidth/src/types.rs` (for `MAX_SUBSCRIPTIONS`) and the full `on_accept` credit-append logic, which I did not get to inspect in full before running out of iterations.

### Title
Unprivileged bandwidth-purchase griefing can evict a victim's prepaid subscription via the 1024-entry FIFO cap - (File: modules/pallets/bandwidth/src/lib.rs)

### Summary
`BandwidthManager.purchase()` lets any caller credit bandwidth to an arbitrary `(chain, app)` pair. `pallet-bandwidth` stores subscriptions per `(chain, app)` in a FIFO `BoundedVec` capped at `MAX_SUBSCRIPTIONS`; once full, the oldest subscription is silently evicted (`SubscriptionEvicted`) regardless of how much was paid or how much time remains before expiry.

### Finding Description
Because purchases are open to any address and keyed purely by the `(app_chain, app)` fields in the message body rather than by payer identity, there is no mechanism preventing a non-owner from spamming cheap, minimal-tier purchases against a victim's app. Repeating this until the list hits its cap forces eviction from the FIFO head, which can destroy the victim's higher-value, unexpired subscription before it is ever drawn down by the gate.

### Impact Explanation
This is a loss-of-funds vector against legitimate protocol users: the victim paid real fee-token value for a byte allowance that is destroyed by a third party with no economic benefit accruing to the victim, purely to force eviction. This matches the required impact class ("stealing or loss of funds") because the victim's prepaid bandwidth balance is provably and permanently destroyed on-chain (`SubscriptionEvicted { lost_bytes }`) by an unprivileged, non-owner actor.

### Likelihood Explanation
Likelihood depends on the economic cost of driving 1024 purchases against a specific `(chain, app)` target relative to the value of the subscription being evicted; I could not confirm the cheapest tier price or whether a minimum-months/minimum-price floor exists that would make this prohibitively expensive. If the cheapest tier is inexpensive relative to a high-tier subscription's value, the attack is directly profitable as pure griefing (e.g., targeting a competitor's app or a protocol-sponsored app) even without direct attacker profit.

### Recommendation
Do not allow the FIFO cap to be reached via unauthenticated/third-party purchases without protecting already-active, unexpired, high-value subscriptions from eviction. Options: (1) require the purchasing manager's `request.from` to match the app being credited (or an explicit allowlist of sponsors) unless intentional sponsorship is being used, (2) evict based on remaining value/bytes rather than pure insertion order, or (3) rate-limit/charge a minimum per-purchase amount high enough that spam-eviction is not economically trivial relative to the smallest tier's value.

### Proof of Concept
Conceptual PoC (not executed against a live chain):
1. Victim calls `BandwidthManager.purchase(app=V, tier=TierFour, months=12, chain="EVM-X")`, crediting a large, long-duration subscription to `(chain, V)`.
2. Attacker repeatedly calls `BandwidthManager.purchase(app=V, tier=TierOne, months=1, chain="EVM-X")` from an unrelated address, 1024+ times (or however many are needed given existing queue depth), each one a valid but cheap purchase.
3. Each purchase dispatches a `BandwidthPurchaseMsg` accepted by `pallet-bandwidth::on_accept`, which appends to the `(chain, V)` `SubscriptionList`; once the list is full, insertion evicts the oldest entry (the victim's Tier-Four subscription if it was inserted before the spam wave, or is pushed out as the spam entries queue ahead in FIFO order).
4. `SubscriptionEvicted { app_chain, app: V, tier: TierFour, lost_bytes }` fires, confirming the victim's paid-for allowance is destroyed, while the attacker's cheap Tier-One entries occupy the queue.

### Citations

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L75-77)
```text
### Eviction

Pushing onto a full list (1024 entries) evicts the **oldest** entry and emits `SubscriptionEvicted` with the lost bytes so the loss is auditable on-chain. In practice this only happens under pathological repeat-buy behavior — at the default of one purchase per cycle, 1024 buys is years of headroom.
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L110-114)
```text
The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
```

**File:** modules/pallets/bandwidth/src/lib.rs (L16-30)
```rust
//! # pallet-bandwidth
//!
//! Prepaid `(chain, app)` byte balances credited by tier purchases
//! from `BandwidthManager.sol`. Each purchase carries its own
//! `app_chain`, so any deployment can sponsor any app on any chain.
//!
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

**File:** modules/pallets/bandwidth/src/lib.rs (L168-175)
```rust
		/// The 1024-cap pushed out the oldest subscription. `lost_bytes`
		/// is what the user paid for and won't get to use.
		SubscriptionEvicted {
			app_chain: StateMachine,
			app: AppKey,
			tier: TierIndex,
			lost_bytes: BandwidthBytes,
		},
```

**File:** evm/src/apps/BandwidthManager.sol (L29-40)
```text
/// Wire payload dispatched by `purchase()` to `pallet-bandwidth`. The
/// pallet credits a tier-bucket on `chain` for `app`, scaled by `months`.
struct BandwidthPurchaseMsg {
    /// Recipient app whose bandwidth is being topped up.
    bytes app;
    /// Tier discriminant (matches `pallet_bandwidth::TierIndex`).
    uint256 tier;
    /// Number of tier-windows to credit. Bytes and duration both scale.
    uint256 months;
    /// UTF-8 chain id like `"EVM-8453"` or `"EVM-137"`.
    bytes chain;
}
```

**File:** docs/content/developers/evm/bandwidth/purchasing.mdx (L14-28)
```text
```solidity title="BandwidthManager.sol" lineNumbers
function purchase(
    bytes calldata app,
    uint256 tier,
    uint256 months,
    bytes calldata chain
) external returns (bytes32 commitment);
```

| Parameter | Description |
|-----------|-------------|
| `app` | Recipient app identifier on the credit chain. Usually a 20-byte EVM address packed as `bytes`. The pallet truncates to a 32-byte `AppKey`. |
| `tier` | Tier discriminant. Must match a configured `TierIndex` variant (`1`, `2`, `3`, or `4`) and must have a non-zero `tierPrice[tier]` on the manager. |
| `months` | Multiplier on `tier.bytes` and `tier.duration_secs`. Must be `> 0`. |
| `chain` | UTF-8 chain id of the **credit chain** — e.g. `"EVM-8453"` for Base or `"EVM-137"` for Polygon. Does not need to equal the source chain (see [Sponsorship](#sponsoring-another-chain)). |
```
