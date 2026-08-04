### Title
Loss of prepaid bandwidth via FIFO-eviction griefing of a victim's `(app_chain, app)` subscription — ([File: modules/pallets/bandwidth/src/lib.rs])

### Summary
The Soroswap bug is about unbounded storage growth from a public `create_pair` letting attackers exhaust storage. `pallet-bandwidth` already avoids the *storage-exhaustion* variant of this bug by bounding the per-`(chain, app)` subscription list to 1024 entries with FIFO eviction of the oldest item [1](#0-0) . However, the same "attacker floods a shared list with entries" primitive resurfaces as a fund-loss bug: the list is keyed by an attacker-choosable `(app_chain, app)` pair rather than by payer, and eviction always removes the *oldest* entry regardless of its remaining value, letting anyone permanently destroy an app's already-paid bandwidth allowance for the cost of enough cheap purchases.

### Finding Description
`Allowance` stores a `BoundedVec<Subscription, 1024>` per `(app_chain, app)` [2](#0-1) . Any purchase message accepted from a registered `BandwidthManager` is credited into this list keyed by the `app_chain`/`app` fields taken *from the message body*, not from the payer's identity — this is explicitly the "sponsorship" model: "a buyer on Ethereum can credit an app on Base... The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`" [3](#0-2) .

`push_subscription` appends every new purchase to the tail and, once the list is at the 1024 cap, unconditionally evicts index 0 — the oldest subscription — irrespective of how many bytes remain on it or how much the original buyer paid: [4](#0-3) 

Because `purchase()` is a fully public, permissionless entrypoint on `BandwidthManager.sol` for *any* configured tier (including the cheapest, e.g. TierOne), and the pallet enforces no per-payer rate limit, no minimum value floor tied to what's being evicted, and no restriction on who may target a given `(app_chain, app)` pair, an attacker can:
1. Observe a victim app has a large/valuable subscription enqueued for `(app_chain, app)`.
2. Repeatedly call `purchase()` for the cheapest tier against that same `(app_chain, app)`, each call appending a new low-value `Subscription`.
3. Once enough cheap purchases have filled the list past the victim's entry, the next purchase evicts it via `list.remove(0)`, permanently destroying the victim's remaining paid-for bytes; only a bookkeeping event (`SubscriptionEvicted`) is emitted with no refund path [5](#0-4) .

The gate that drains bandwidth trusts this FIFO ordering unconditionally and has no defense against value-blind eviction [6](#0-5) . Nothing in `is_purchase_message` or the `on_accept` credit path validates that the entity crediting a given `(app_chain, app)` bucket is related to or authorized by the app being credited — that's is by design for sponsorship [7](#0-6) , but it also means eviction targeting is equally unauthenticated.

### Impact Explanation
This is a direct loss-of-funds primitive: a victim app that legitimately purchased bandwidth (paying real fee-token value for a large byte allowance with a long duration) can have that allowance permanently destroyed by an unprivileged attacker who only needs to pay for enough cheap tier purchases to push the victim's entry out of the bounded FIFO. The victim receives no compensation and the destroyed bytes are unrecoverable — matching the bounty's "stealing or loss of funds" impact class, analogous to how the Soroswap bug let an attacker corrupt/exhaust a critical piece of shared state through unrestricted, cheap repeated calls to a public function.

### Likelihood Explanation
The attack requires no relayer, prover, admin, or leaked key — only calling the public `purchase()` function on `BandwidthManager.sol` (an EVM contract with no special permission requirements) enough times against the cheapest configured tier, targeting the same `(app_chain, app)` as the victim. The cost to the attacker scales with the cheapest tier's price times the number of evictions needed to reach the victim's queue position, which can be made economically favorable to the attacker if the value of the destroyed subscription (large tier × many months) exceeds the cumulative cost of cheap eviction purchases. This is realistically executable by any market participant with knowledge of a victim's `(app_chain, app)` allowance depth (readable via `Pallet::allowances`) [8](#0-7) .

### Recommendation
- Evict based on remaining value (e.g., lowest `remaining_bytes` or soonest-expiring) rather than strictly FIFO oldest, or
- Enforce a minimum "byte-weight" cost per queue slot so it is never cheaper to evict a slot than the value it holds, or
- Separate subscriptions by payer/source rather than merging all sponsors into a single shared, evictable queue per `(app_chain, app)`, or
- Add a per-`(app_chain, app)` rate limit / cooldown on purchase-driven pushes to prevent rapid queue flooding.

### Proof of Concept
1. Victim buys `TierFour` (largest byte/duration allowance) for `(app_chain=Base, app=X)` via `BandwidthManager.purchase()`; this appends a large `Subscription` to `Allowance::<T>::get(Base, X)`.
2. Attacker repeatedly calls `purchase()` on the same `BandwidthManager` for `TierOne` (cheapest, `months=1`) with `chain="EVM-8453"`, `app=X`, sending enough purchase messages that the FIFO list at `(Base, X)` fills to the 1024 cap and advances past the victim's position.
3. On the next attacker purchase past the cap, `push_subscription` calls `list.remove(0)`, evicting the victim's large `Subscription` and emitting `SubscriptionEvicted { lost_bytes: <victim's remaining bytes> }` [9](#0-8) .
4. Victim's app now has zero or drastically reduced bandwidth despite having paid for a large, long-duration allowance; the attacker paid only the cumulative cost of cheap `TierOne` purchases needed to reach and evict that slot.

### Citations

**File:** modules/pallets/bandwidth/src/types.rs (L19-22)
```rust
/// Hard cap on the subscription list per `(chain, app)`. Pushes
/// beyond this evict the oldest entry (FIFO).
pub const MAX_SUBSCRIPTIONS: u32 = 1024;
pub type MaxSubscriptions = ConstU32<MAX_SUBSCRIPTIONS>;
```

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

**File:** modules/pallets/bandwidth/src/lib.rs (L360-370)
```rust
	impl<T: Config> Pallet<T> {
		/// Non-expired subscriptions for `(app_chain, app)` in insertion
		/// (= FIFO drain) order. Read-only snapshot.
		pub fn allowances(app_chain: &StateMachine, app: &[u8]) -> Vec<Subscription> {
			let key = AppKey::truncate_from(app.to_vec());
			let now = <T as pallet_ismp::Config>::TimestampProvider::now().as_secs();
			Allowance::<T>::get(app_chain, &key)
				.into_iter()
				.filter(|s| s.expires_at > now)
				.collect()
		}
```

**File:** modules/pallets/bandwidth/src/lib.rs (L404-434)
```rust
		fn push_subscription(
			app_chain: &StateMachine,
			app: &AppKey,
			tier: TierIndex,
			bytes: BandwidthBytes,
			duration_secs: u64,
		) -> u64 {
			let now = <T as pallet_ismp::Config>::TimestampProvider::now().as_secs();
			let expires_at = now.saturating_add(duration_secs);
			let new_sub =
				Subscription { tier, remaining_bytes: bytes, expires_at, purchased_at: now };

			let evicted = Allowance::<T>::mutate(app_chain, app, |list| {
				let evicted = if list.len() == MAX_SUBSCRIPTIONS as usize {
					Some(list.remove(0))
				} else {
					None
				};
				// Capacity is now guaranteed; try_push can't fail.
				let _ = list.try_push(new_sub);
				evicted
			});

			if let Some(old) = evicted {
				Self::deposit_event(Event::SubscriptionEvicted {
					app_chain: *app_chain,
					app: app.clone(),
					tier: old.tier,
					lost_bytes: old.remaining_bytes,
				});
			}
```

**File:** modules/pallets/bandwidth/src/lib.rs (L439-445)
```rust
		/// The router uses this to skip the gate on purchases —
		/// otherwise a depleted app couldn't recharge.
		pub fn is_purchase_message(request: &PostRequest) -> bool {
			BandwidthManager::<T>::get(&request.source)
				.map(|m| request.from == m.0.to_vec())
				.unwrap_or(false)
		}
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L83-93)
```text
## The Gate

Every non-purchase POST request from a registered source chain runs through `BandwidthGate::try_consume(source, app, bytes)`:

1. If the app is on the **allowlist** for that source, return `Ok` without touching the ledger.
2. Sweep expired subscriptions in place.
3. If no live subscriptions remain → `GateError::NoAllowance`.
4. Sum `remaining_bytes` across live entries. If the sum is short → `GateError::Insufficient { remaining, required }`. **No mutation happens in this case** — the caller can retry after a top-up.
5. Otherwise drain from the head until the requested bytes are satisfied. Pop entries that hit zero. Emit `BandwidthConsumed` with the post-deduct remaining.

The "no mutation on insufficient" property is load-bearing: it means a top-up race is safe — if a message arrives between when an app notices it's short and when the top-up lands, the message stays rejectable rather than half-consuming the subscription.
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L108-114)
```text
## Sponsorship

The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
```
