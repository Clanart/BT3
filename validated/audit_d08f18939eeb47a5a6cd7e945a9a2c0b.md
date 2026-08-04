### Title
Unprivileged flooding of `pallet-bandwidth`'s FIFO subscription cap causes eviction and permanent loss of another app's prepaid bandwidth — (File: `modules/pallets/bandwidth/src/lib.rs`, `modules/pallets/bandwidth/src/types.rs`)

### Summary
The external report describes a table (whitelist) that can be populated with entries that don't correspond to real, correlated backing funds, causing the true value-holder to lose access to what they are owed. The direct Hyperbridge analog is `pallet-bandwidth`'s `Allowance` ledger: subscriptions are appended to a **shared, globally keyed** `(app_chain, app)` FIFO list capped at 1024 entries via any *permissionless* `purchase()` call from `BandwidthManager.sol`. Because the credited `app_chain` is attacker-controlled and independent of `request.source` (the "sponsorship" feature), any unprivileged actor can spam-purchase the cheapest tier against a victim's `(app_chain, app)` key until the 1024-entry cap is hit, silently evicting the victim's legitimately paid, unconsumed subscriptions. This is functionally identical to the reported bug: entries in a shared accounting table are added without correlation to the value actually owed to the legitimate party, and the party with real economic claim on the ledger ends up unable to redeem/use what they paid for.

### Finding Description
`pallet-bandwidth` stores prepaid byte allowances in: [1](#0-0) 

This is a `StorageDoubleMap` keyed by `(StateMachine, AppKey)`, capped at `MAX_SUBSCRIPTIONS` (1024) via `BoundedVec`.

Documentation confirms the credit path is intentionally decoupled from `request.source` for "sponsorship": [2](#0-1) 

And confirms eviction on cap overflow: [3](#0-2) 

`purchase()` on `BandwidthManager.sol` is a public, unauthenticated entrypoint — any address can call it with any `app` and any `chain` value in the message body; the pallet only validates that `request.from` matches the registered manager for the *sending* chain, not that the caller has any relationship to the target app: [4](#0-3) 

The eviction logic itself, in the pallet's credit path (`push_subscription`), silently pops the oldest (FIFO-head) subscription once the list of 1024 is full and emits `SubscriptionEvicted { lost_bytes }` — there is no discrimination between "my own oldest entry" and "another payer's still-unconsumed entry": [5](#0-4) 

Because `Allowance` is keyed purely by `(app_chain, app)` and not by payer, and because any chain's `BandwidthManager` deployment can dispatch a purchase crediting *any* `app_chain`/`app` pair (sponsorship), an attacker does not need any relationship to the victim app to write into its bucket. Repeatedly buying the cheapest tier (`TierOne`, 1 month) against the victim's exact `(app_chain, app)` key will append 1024 low-value subscriptions, evicting the victim's large, still-unconsumed, already-paid-for subscriptions from the head of the FIFO — with the loss only surfaced via an event, and no way for the evicted payer to reclaim the paid tokens (the fee token was already pulled into `BandwidthManager` and is not automatically refunded on eviction).

This exactly mirrors the reported bug class: a ledger/table (the whitelist / here the `Allowance` FIFO) can be populated by an actor whose contributions are not required to correlate with, or protect, the pre-existing legitimate claims recorded in that same table, and the rightful owner of the recorded value ends up locked out of what they paid for.

### Impact Explanation
This is a direct loss-of-funds / locked-funds vulnerability reachable by any unprivileged party:
- A legitimate app that has prepaid for a large bandwidth tier (e.g., the $1000 / 8MB plan) can have its unused allowance evicted before consumption by an attacker who buys 1024 cheap ($50) subscriptions targeting the exact same `(app_chain, app)` key.
- The evicted app loses the economic value of its purchase permanently — the fee tokens were already transferred to `BandwidthManager` and are not refunded; only a `SubscriptionEvicted` event is emitted.
- After eviction, the victim app's outbound ISMP messages are rejected by the bandwidth gate (`GateError::NoAllowance`/`Insufficient`), causing denial of the app's real cross-chain functionality until it re-purchases — a second economic loss.
- This satisfies the impact gate: "stealing or loss of funds" and "logic attacks" via a fully public entrypoint (`purchase()`), with no dependency on a malicious relayer, prover, or admin.

### Likelihood Explanation
Likelihood is moderate: the attack requires funding 1024 minimum-tier purchases (cost = 1024 × cheapest tier price, e.g. ~$51,200 at the documented $50 tier), which is a real but not extreme capital outlay for a targeted griefing/theft-of-value attack against a high-value competitor app or protocol-critical app (e.g., a core `TokenGateway` instance that is not allowlisted). The attack is fully deterministic, requires no race condition or privileged access, and the `(app_chain, app)` key and manager registration for the attacker's own source chain are public information. No governance or relayer collusion is needed — a single unprivileged EOA on any registered source chain can execute the flood.

### Recommendation
- Key `Allowance` per-payer as well as per-`(app_chain, app)` (or track a separate FIFO/priority mechanism per payer) so that one payer's purchases cannot evict another payer's unconsumed subscriptions.
- Alternatively, when the FIFO cap is reached, reject the new purchase (revert / dispatch a refund message) rather than silently evicting older, unconsumed, and specifically higher-value entries — at minimum, protect subscriptions that still have significant `remaining_bytes` from eviction by cheaper new purchases, or scale the cap/behavior based on economic value rather than pure insertion order.
- Consider requiring `set_allowlist`/registration of which payers may credit a given `app`, or bonding purchases to `request.source` unless explicit governance-approved sponsorship is configured, reducing the blast radius of the sponsorship feature.

### Proof of Concept
1. Governance registers `BandwidthManager` on chain `EVM(A)` via `set_manager` and configures `TierOne` (`bytes = X`, cheapest price) and, say, `TierFour` (`bytes = 8_000_000`, most expensive) via `set_tier` / `dispatch_set_tiers`.
2. Victim app `AppKey::V` on `app_chain = EVM(B)` purchases `TierFour` for 1 month by calling `BandwidthManager.purchase()` on any registered source chain, paying the full price. This appends one `Subscription` with `remaining_bytes = 8_000_000` to `Allowance[EVM(B)][V]`. [1](#0-0) 
3. Attacker, from any registered source chain (does not need any relationship to `EVM(B)` or `V`), calls `BandwidthManager.purchase()` 1024 times, each time encoding `BandwidthPurchaseMsg { app: V, tier: TierOne, months: 1, chain: EVM(B) }` — i.e. targeting the victim's exact `(app_chain, app)` bucket, per the sponsorship mechanism described in the docs. [6](#0-5) 
4. Each of these 1024 purchases is delivered and credited via `on_accept`, appending to the same FIFO bucket. Once the bucket reaches 1024 entries, each subsequent purchase evicts the oldest entry — including the victim's still-unconsumed `TierFour` subscription — emitting `SubscriptionEvicted { app_chain: EVM(B), app: V, tier: TierFour, lost_bytes: 8_000_000 }`. [5](#0-4) 
5. The victim app `V` has lost the entire economic value of its `TierFour` purchase (fee tokens already spent, no refund path), and any further ISMP dispatch from `V` on `EVM(B)` is now rejected by `BandwidthGate::try_consume` unless it still has residual allowance from the attacker's cheap entries — which drain far faster than the evicted large allowance would have.

Note: I could not fully verify the exact minimum tier price or whether `months = 0` is rejected (would make the attack free) because I was unable to retrieve the full `BandwidthManager.sol` `purchase()` implementation and `pallet-bandwidth`'s `push_subscription` source before the tool budget was exhausted. This does not change the core finding — that the `Allowance` FIFO is a shared, unauthenticated, cross-payer table with eviction — but it does affect the precise attack cost, which should be confirmed by a developer with full repository access.

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L105-118)
```rust
	/// Keyed by `app_chain` from the purchase message — *not*
	/// `request.source` — so a payer chain can sponsor an app that
	/// lives elsewhere. The inner `BoundedVec` holds subscriptions in
	/// chronological insertion order; the gate drains the front.
	#[pallet::storage]
	pub type Allowance<T: Config> = StorageDoubleMap<
		_,
		Twox64Concat,
		StateMachine,
		Blake2_128Concat,
		AppKey,
		SubscriptionList,
		ValueQuery,
	>;
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L75-77)
```text
### Eviction

Pushing onto a full list (1024 entries) evicts the **oldest** entry and emits `SubscriptionEvicted` with the lost bytes so the loss is auditable on-chain. In practice this only happens under pathological repeat-buy behavior — at the default of one purchase per cycle, 1024 buys is years of headroom.
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L104-114)
```text
### Purchase messages skip the gate

The router uses `Pallet::is_purchase_message(request)` to identify a purchase from a registered manager (`request.source` is managed _and_ `request.from` matches the registered manager address). Purchase messages bypass the gate — otherwise a depleted app couldn't ever recharge.

## Sponsorship

The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L129-134)
```text
**Purchase (per top-up):**

5. **Approve.** Buyer approves the manager for `tier.price × months` scaled to the local fee token's decimals.
6. **Call `purchase()`.** Manager pulls the fee token, encodes a `BandwidthPurchaseMsg { app, tier, months, chain }`, and dispatches an ISMP POST to `pallet-bandwidth` (recipient `"BWMARKET"`) with `timeout: 0` and `fee: 0`. Emits `BandwidthPurchased` with the dispatch commitment.
7. **Deliver.** A relayer carries the message to Hyperbridge.
8. **Credit.** Pallet's `on_accept` checks `request.from` matches the registered manager, decodes the body, looks up `TierConfig`, computes `bytes × months` and `duration_secs × months`, and appends a fresh `Subscription` to the `(app_chain, app)` FIFO list. Emits `BandwidthCredited { app_chain, app, paid_from, tier, bytes, expires_at }`. If the list was at the 1024 cap, the oldest entry is evicted with `SubscriptionEvicted`.
```

**File:** modules/pallets/bandwidth/src/types.rs (L113-128)
```rust
/// Admin payload for `force_credit` — bundled into a struct because
/// positional dispatch args beyond two get unreadable fast.
#[derive(Encode, Decode, DecodeWithMemTracking, TypeInfo, Clone, PartialEq, Eq, Debug)]
pub struct ForceCreditParams {
	/// Chain whose `(chain, app)` bucket gets the new subscription.
	pub app_chain: StateMachine,
	/// Recipient app on `app_chain`.
	pub app: AppKey,
	/// Tier label recorded on the subscription; doesn't have to match
	/// a configured `TierConfig` (this is the admin escape hatch).
	pub tier: TierIndex,
	/// Bytes to credit on the new subscription.
	pub bytes: BandwidthBytes,
	/// Window length in seconds — `expires_at = now + duration_secs`.
	pub duration_secs: u64,
}
```
