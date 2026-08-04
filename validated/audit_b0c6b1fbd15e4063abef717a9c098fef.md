Confirmed: `purchase()` in `BandwidthManager.sol` is fully permissionless — any caller can pay for and credit bandwidth to *any* `app`/`chain` pair, and `pallet-bandwidth::push_subscription` evicts the oldest entry once the FIFO list hits the hard cap of `MAX_SUBSCRIPTIONS = 1024`, with no minimum-value or ownership check on what gets evicted.

### Title
Permissionless bandwidth purchase lets an attacker evict a victim's paid subscriptions via the 1024-cap FIFO queue - (File: `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`pallet-bandwidth` stores each `(app_chain, app)`'s prepaid bandwidth as a FIFO `BoundedVec<Subscription, 1024>` (`Allowance` storage, `MAX_SUBSCRIPTIONS`). Any address can call `BandwidthManager.purchase(app, tier, months, chain)` on any EVM chain to credit *any* `app` (identified only by raw bytes, not by ownership) with a cheap tier subscription. Once the queue for that `(chain, app)` reaches 1024 entries, each further purchase evicts the oldest entry via `Allowance::mutate` → `list.remove(0)` in `push_subscription`, regardless of that entry's remaining value, and emits `SubscriptionEvicted` with `lost_bytes` as the only record.

### Finding Description
The root cause mirrors the seed report's core invariant: unbounded/attacker-influenced item accumulation forces a shared, order-sensitive structure to be walked and mutated for every actor, with no accounting for whose value is being displaced.

- `BandwidthPurchaseMsg.app` and `.chain` are attacker-controlled parameters of `purchase()`; there's no check binding `msg.sender` to `app` — see `evm/src/apps/BandwidthManager.sol:148-193`.
- On the pallet side, `on_accept` only verifies the *manager contract* identity (`request.from == manager`), not who benefits from the credit: [1](#0-0) 
- `push_subscription` unconditionally evicts index `0` (the oldest, not the smallest/least-valuable) once the list is full, with no floor on how cheap the evicting purchase can be: [2](#0-1) 
- The cap is fixed at 1024 regardless of tier size, so an attacker only needs to buy `TierOne` (cheapest, smallest `bytes`/`duration_secs`) 1024 times to fully displace whatever a victim previously paid for — even a single large multi-month `TierFour` purchase: [3](#0-2) 

This is directly analogous to the seed bug's core flaw: a shared queue/array that any unprivileged actor can force to grow, causing state that legitimately belongs to another party to be silently destroyed/displaced as a side effect of normal operation (here, `push_subscription`'s eviction instead of the Skale slashing loop). No relayer, prover, governance actor, or leaked key is required — only ordinary token approval and gas on the source EVM chain.

### Impact Explanation
This is a direct loss-of-funds / logic-attack vector matching the bounty's "stealing or loss of funds" and "logic attacks" categories:
- A victim app that pays for a large, expensive `TierFour` multi-month subscription can have it evicted before consumption by an attacker spamming 1024 cheap `TierOne` purchases against the same `(chain, app)` key.
- The victim's `remaining_bytes` for the evicted subscription is permanently lost (`SubscriptionEvicted.lost_bytes`), while the attacker's tiny purchases occupy the queue.
- Because `try_consume` drains strictly FIFO and never re-derives value, the app is left able to consume only the attacker's cheap credits, and the ISMP gate (`BandwidthGate::try_consume`) will reject legitimate high-bandwidth messages the victim already paid for, causing denial of message delivery for that app in addition to the fund loss.

### Likelihood Explanation
High. The attack requires only:
1. Ability to call `purchase()` (fully public, no allowlist), and
2. Enough fee-token balance to buy the cheapest tier ~1024 times (bounded, predictable cost, not a network-wide gas-limit DoS — it's a deliberate, affordable griefing/asset-destruction primitive).
No relayer collusion, no consensus-proof forgery, and no governance action is needed; the flow goes through the normal `on_accept` purchase path that is explicitly designed to be open ("any deployment can sponsor any app on any chain").

### Recommendation
- Bind purchases to app ownership, or at minimum weight eviction by value: evict based on remaining value/tier rather than pure insertion order, or reject/refuse further pushes once the queue is full instead of silently evicting.
- Consider a minimum-tier-value floor for eviction eligibility (e.g., only evict entries with `remaining_bytes` below the incoming purchase's size), or require the evicting purchase to be at least as valuable as what it displaces.
- Alternatively, make `MAX_SUBSCRIPTIONS` per-payer or require `purchase()` to be permissioned/attributable to the `app` being credited, closing the "spend a little to destroy someone else's large prepaid balance" primitive.

### Proof of Concept
1. Victim (or protocol on victim's behalf) calls `BandwidthManager.purchase(appBytes, TierFour, 12, chainBytes)`, paying full price for 12 months of the largest tier; pallet credits one `Subscription` entry with large `remaining_bytes`/`expires_at` at position 0 (or wherever it lands in the FIFO).
2. Attacker (unprivileged, no relationship to victim) repeatedly calls `purchase(appBytes, TierOne, 1, chainBytes)` — the same `app`/`chain` pair — 1024 times, each dispatching a minimal `BandwidthPurchaseMsg` via the open `purchase()` entrypoint (`evm/src/apps/BandwidthManager.sol:148`).
3. Each inbound `on_accept` on hyperbridge calls `push_subscription`, which appends the attacker's cheap subscription and, once `Allowance::<T>::get(app_chain, app).len() == 1024`, evicts index `0` — eventually evicting the victim's still-unconsumed `TierFour` subscription (`modules/pallets/bandwidth/src/lib.rs:416-425`).
4. `SubscriptionEvicted { lost_bytes: <victim's remaining_bytes> }` fires; the victim's prepaid bandwidth is gone, and subsequent `try_consume` calls for that app can only draw from the attacker's negligible remaining credits, causing legitimate messages to be rejected with `GateError::Insufficient`/`NoAllowance`.

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L404-437)
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

			expires_at
		}
```

**File:** modules/pallets/bandwidth/src/lib.rs (L454-477)
```rust
	impl<T: Config> IsmpModule for Pallet<T> {
		fn on_accept(&self, request: PostRequest) -> Result<Weight, anyhow::Error> {
			let manager = BandwidthManager::<T>::get(&request.source).ok_or_else(|| {
				anyhow::anyhow!(format!("no bandwidth manager registered for {:?}", request.source))
			})?;

			if request.from != manager.0.to_vec() {
				return Err(anyhow::anyhow!(format!(
					"purchase from unauthorised sender on {:?}: expected {:x?}, got {:x?}",
					request.source, manager.0, request.from
				)));
			}

			let msg = PurchaseMessage::try_from(request.body.as_slice())?;
			let tier = TierIndex::try_from(msg.tier)
				.map_err(|_| anyhow::anyhow!(format!("unknown tier discriminant {}", msg.tier)))?;
			let cfg = Tiers::<T>::get(tier)
				.ok_or_else(|| anyhow::anyhow!(format!("tier {:?} is not configured", tier)))?;

			let bytes = cfg.bytes.saturating_mul(msg.months as u128);
			let duration = cfg.duration_secs.saturating_mul(msg.months as u64);

			let key = AppKey::truncate_from(msg.app);
			let expires_at = Self::push_subscription(&msg.chain, &key, tier, bytes, duration);
```

**File:** modules/pallets/bandwidth/src/types.rs (L19-22)
```rust
/// Hard cap on the subscription list per `(chain, app)`. Pushes
/// beyond this evict the oldest entry (FIFO).
pub const MAX_SUBSCRIPTIONS: u32 = 1024;
pub type MaxSubscriptions = ConstU32<MAX_SUBSCRIPTIONS>;
```
