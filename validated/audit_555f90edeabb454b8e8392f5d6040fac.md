## Title
Permissionless FIFO subscription flooding forces eviction of another app's paid, unexpired bandwidth allowance - (File: `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`pallet-bandwidth`'s `on_accept` handler credits a `(app_chain, app)` subscription bucket purely from the `chain`/`app` fields embedded in a purchase message body, not from the caller's identity. Any account can call `BandwidthManager.purchase()` on a registered source chain and target *any* `app_chain`/`app` pair, paying for the cheapest configured tier. Each purchase pushes into a `BoundedVec` capped at 1024 entries; once the cap is reached, `push_subscription` unconditionally evicts index `0` — the oldest entry — regardless of whether it still has `remaining_bytes` or time left before `expires_at`. This mirrors the reported `batchRelease()` bug class: an unprivileged actor can flood a shared, no-skip, capacity-bounded structure with minimal-cost entries to damage a legitimate party's position, except here the outcome is not merely delay/gas cost but outright loss of another party's already-paid, unconsumed bandwidth credit.

### Finding Description
`Allowance<T>` is a `StorageDoubleMap<StateMachine, AppKey, SubscriptionList>` where `SubscriptionList = BoundedVec<Subscription, MaxSubscriptions>` (cap 1024). [1](#0-0) 

`on_accept` derives the target bucket entirely from the message body (`msg.chain`, `msg.app`), which the *payer* controls, not from `request.source`/`request.from` identity beyond validating that the message came from *a* registered manager on *some* source chain: [2](#0-1) 

This is documented as an intentional sponsorship feature — "a buyer on Ethereum can credit an app on Base" — but it also means nothing prevents a hostile third party from repeatedly crediting a *victim's* bucket with the cheapest configured tier purely to consume queue slots.

`push_subscription` performs unconditional eviction on cap overflow: [3](#0-2) 

`list.remove(0)` always evicts the chronologically oldest entry — including one with a large `remaining_bytes` balance and a distant `expires_at` — with no check for value, remaining bytes, or elapsed lifetime. The eviction is only "auditable" via `SubscriptionEvicted`, not preventable or refundable.

The broken invariant: a resource that a legitimate purchaser paid real money for (`remaining_bytes` of an unexpired `Subscription`) can be irrecoverably destroyed by an unrelated, unprivileged actor who only pays for cheap tier purchases, because the FIFO admission path enforces no per-`(chain, app)` write authorization and no minimum value/size floor tying spam cost to the value being destroyed.

### Impact Explanation
This is a direct loss-of-funds primitive, not a generic gas/DoS complaint:
- A victim app's or sponsor's paid bandwidth allocation (real, spent fee-token value credited via `BandwidthCredited`) is silently and permanently destroyed via forced FIFO eviction, before it is ever consumed.
- The victim has no defense: the gate's "no mutation on insufficient" property (`try_consume`) protects against partial consumption races, but it does nothing against eviction from unrelated purchase traffic, since `push_subscription` is an entirely separate, permissionless write path keyed the same way.
- Once evicted, the bytes are unrecoverable — there is no re-credit or refund mechanism for `SubscriptionEvicted` entries in `modules/pallets/bandwidth/src/lib.rs`.

### Likelihood Explanation
The attack requires only:
1. A registered `BandwidthManager` on any source chain (already exists in production per the bounty's live-deployment scope).
2. Repeated calls to `purchase()` targeting the victim's known `(app_chain, app)` pair with the cheapest configured tier, up to `MAX_SUBSCRIPTIONS` (1024) minus the victim's existing entry count.
3. No admin, relayer, prover, or governance role is needed — `purchase()` is a public, permissionless EVM entrypoint, and `on_accept` performs no authorization check tying the `chain`/`app` fields to the caller.

The cost to the attacker scales with tier price × number of purchases needed to reach the cap, which is bounded and known in advance (the attacker can observe `Pallet::allowances(app_chain, app)` to determine exactly how many pushes are required), making this a deterministic, not probabilistic, attack.

### Recommendation
- Do not evict entries with non-zero `remaining_bytes` and unexpired `expires_at` purely because the queue is full; either reject the purchase (surface a clear error/refund path) or require the evicting purchase to be at least as valuable as what it displaces.
- Alternatively, cap subscriptions per (payer, app) instead of a single shared FIFO per `(app_chain, app)`, or require the credited `app` to be the message sender's own registered identity unless an explicit sponsorship allowlist is set.
- Emit `SubscriptionEvicted` refund-eligible events and add a governance/`force_credit` remediation path keyed to the evicted `Subscription`, so provable unjust eviction can be compensated.

### Proof of Concept
1. Governance registers `BandwidthManager` for `EVM-<chainA>` and configures `TierOne` as the cheapest tier (e.g., minimal `bytes`/`duration_secs`, low price).
2. Alice (a legitimate sponsor) purchases a large `TierFour` allocation for `(app_chain = EVM-8453, app = victimApp)`; `BandwidthCredited` fires, allowance list now has 1 live `Subscription` with large `remaining_bytes` and long `expires_at`.
3. Mallory, an unrelated account, calls `BandwidthManager.purchase()` 1023 times on `EVM-<chainA>`, each time setting the ABI-encoded `PurchaseMessage.chain = EVM-8453` and `PurchaseMessage.app = victimApp`, using `TierOne` (cheapest configured tier).
4. On the 1024th such purchase landing via `on_accept` → `push_subscription`, the list is full; `list.remove(0)` evicts Alice's `TierFour` `Subscription` (`SubscriptionEvicted` emitted) even though it has years of `remaining_bytes` untouched.
5. Alice's paid bandwidth is gone; `victimApp` must be re-funded by Alice from scratch, while Mallory only spent `TierOne` price × 1023 to force the eviction — proportionally cheap versus destroying a `TierFour` (or larger, e.g. sponsor-funded enterprise) allocation. [3](#0-2) [2](#0-1)

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L109-118)
```rust
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

**File:** modules/pallets/bandwidth/src/lib.rs (L400-437)
```rust
		/// Append a fresh subscription with a fixed expiry. If the list
		/// is already at `MaxSubscriptions`, evict the oldest entry and
		/// emit [`Event::SubscriptionEvicted`] so the lost bytes are
		/// auditable. Returns the new subscription's `expires_at`.
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

**File:** modules/pallets/bandwidth/src/lib.rs (L455-489)
```rust
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

			Self::deposit_event(Event::BandwidthCredited {
				app_chain: msg.chain,
				app: key,
				paid_from: request.source,
				tier,
				bytes,
				expires_at,
			});

			Ok(Weight::zero())
		}
```
