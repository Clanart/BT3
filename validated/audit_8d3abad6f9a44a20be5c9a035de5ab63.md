Based on the local codebase, I found a solid analog to the "hardcoded cap silently drops legitimate value" bug class: `pallet-bandwidth`'s fixed 1024-entry FIFO subscription cap, which unconditionally evicts (and permanently discards) a paid-for, unexpired allowance once the cap is hit — regardless of who paid for it or how much of it remains.

### Title
Unprivileged flooding of a victim's bandwidth FIFO queue causes silent, unconditional eviction of paid, unexpired allowance - (File: `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`pallet-bandwidth` stores a `(app_chain, app)`-keyed FIFO list of purchased bandwidth `Subscription`s in a `BoundedVec` capped at `MAX_SUBSCRIPTIONS = 1024` [1](#0-0) . Every successful purchase — from *any* payer on *any* registered source chain, for an `app` chosen by the payer — appends unconditionally to this shared list, and once the list is full, `push_subscription` evicts the oldest entry regardless of its remaining bytes or expiry [2](#0-1) . This mirrors the M-11 bug class exactly: a hardcoded cap that, once exceeded, silently drops legitimate, already-paid-for value instead of reverting or protecting it.

### Finding Description
`on_accept` credits a purchase to `(app_chain, app)` taken straight from the message body — not from `request.source`/`request.from` identity of the payer — so any account on any registered source chain can queue subscriptions against an arbitrary victim `app` [3](#0-2) . The eviction in `push_subscription` is a blind `list.remove(0)` the moment `list.len() == MAX_SUBSCRIPTIONS`, with no check on whether the evicted entry is expired or has bytes remaining [4](#0-3) . The eviction is documented as intentional ("Pushing onto a full list (1024 entries) evicts the oldest entry... lost_bytes is what the user paid for and won't get to use") but the design assumption is benign usage ("at the default of one purchase per cycle, 1024 buys is years of headroom") [5](#0-4) . That assumption does not hold against an adversarial payer who intentionally floods the same `app` key with many cheap, minimal-tier purchases to push the queue past 1024 and force out a victim's legitimate, unexpired, higher-value subscription — the exact "cap set too low, excess entries silently lost" pattern from the M-11 report, except here the excluded value is an already-paid allowance rather than an unpaid royalty share.

### Impact Explanation
A successful flood evicts the victim's paid subscription from storage entirely (`SubscriptionEvicted` fires, but the bytes are gone — `Allowance::<T>` no longer contains the entry), permanently destroying value the victim paid for via `BandwidthManager.purchase()`. This is a loss-of-funds/loss-of-service outcome reachable by an unprivileged, permissionless caller through the normal purchase path — no relayer, prover, or admin compromise required. Because `app_chain`/`app` in the credit message is attacker-controlled and independent of the caller's own identity (the sponsorship feature explicitly allows crediting any app on any chain), any account can target any `(app_chain, app)` pair.

### Likelihood Explanation
The attack is economically bounded — the attacker must pay for enough cheap-tier purchases to fill/refill the queue to 1024 entries before an eviction reaches the target subscription(s) — so it is not free, but it is unconditional and permissionless: no governance, allowlist, or manager-level privilege is required, and the cost is fixed and attacker-controlled (buy the cheapest configured tier repeatedly). Against a victim with only a handful of legitimate subscriptions relative to the 1024 cap, or against a victim whose subscription is worth more than 1024×cheapest-tier-price, this is a favorable griefing/fund-destruction trade for the attacker.

### Recommendation
- Do not evict unconditionally on cap-hit. Either reject/queue the new purchase (with an explicit error) when the oldest entry is still unexpired and has remaining bytes, or refund/credit the evicted value back to its original payer instead of discarding it.
- Consider scoping the eviction-eligible cap or purchase rate per payer identity rather than per `(app_chain, app)`, so an unrelated third party cannot cheaply force eviction of another payer's allowance.
- At minimum, raise `MAX_SUBSCRIPTIONS` cost-of-attack analysis into an explicit, documented risk model (mirroring Foundation's own M-11 resolution: document the limitation and provide a mitigation/workaround) rather than relying on an assumption of non-adversarial purchase cadence.

### Proof of Concept
1. Attacker identifies target `(app_chain, app)` with an existing legitimate subscription (e.g., victim bought the $1000/8MB tier for a full year).
2. Attacker (any account, any source chain with a registered `BandwidthManager`) repeatedly calls `purchase()` with the cheapest configured tier and `months = 1`, targeting the same `app_chain`/`app` in the `BandwidthPurchaseMsg`, as confirmed by the pallet's `test_bandwidth::subscription_cap_evicts_oldest` behavior showing unconditional oldest-first eviction at the 1024 cap [6](#0-5) .
3. After enough purchases to reach 1024 entries plus one more, the pallet evicts the victim's original entry via `push_subscription`'s `list.remove(0)` [4](#0-3) , emitting `SubscriptionEvicted` with the victim's `lost_bytes`, while the victim's paid allowance is permanently gone from `Allowance::<T>`.

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L71-74)
```rust
	/// FIFO list of subscriptions stored per `(chain, app)`, bounded
	/// by [`MAX_SUBSCRIPTIONS`]. Pushes onto a full list evict the
	/// oldest entry.
	pub type SubscriptionList = BoundedVec<Subscription, MaxSubscriptions>;
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

**File:** modules/pallets/bandwidth/src/lib.rs (L454-489)
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

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L75-77)
```text
### Eviction

Pushing onto a full list (1024 entries) evicts the **oldest** entry and emits `SubscriptionEvicted` with the lost bytes so the loss is auditable on-chain. In practice this only happens under pathological repeat-buy behavior — at the default of one purchase per cycle, 1024 buys is years of headroom.
```

**File:** modules/pallets/testsuite/src/tests/pallet_bandwidth.rs (L523-574)
```rust
/// The 1024-sub cap evicts the oldest entry. force_credit reuses the
/// same push path as purchase, so this also covers the purchase cap.
#[test]
fn subscription_cap_evicts_oldest() {
	new_test_ext().execute_with(|| {
		jump_to(T0);
		let cap = MAX_SUBSCRIPTIONS as u128;

		// Fill the list to exactly the cap. `bytes` encodes the index
		// so we can prove which one got evicted.
		for i in 0..cap {
			Bandwidth::force_credit(
				RuntimeOrigin::root(),
				ForceCreditParams {
					app_chain: APP_CHAIN,
					app: app_key(),
					tier: TIER1,
					bytes: i + 1,
					duration_secs: MONTH_SECS,
				},
			)
			.unwrap();
		}
		assert_eq!(sub_count(APP_CHAIN), cap as usize);
		assert_eq!(sub_at(APP_CHAIN, 0).unwrap().remaining_bytes, 1, "oldest is index 1");

		// One more push: evicts the oldest, appends the new one.
		Bandwidth::force_credit(
			RuntimeOrigin::root(),
			ForceCreditParams {
				app_chain: APP_CHAIN,
				app: app_key(),
				tier: TIER1,
				bytes: cap + 1,
				duration_secs: MONTH_SECS,
			},
		)
		.unwrap();

		assert_eq!(sub_count(APP_CHAIN), cap as usize, "still capped");
		assert_eq!(
			sub_at(APP_CHAIN, 0).unwrap().remaining_bytes,
			2,
			"former second-oldest is now front",
		);
		assert_eq!(
			sub_at(APP_CHAIN, (cap - 1) as usize).unwrap().remaining_bytes,
			cap + 1,
			"new sub is at the back",
		);
	});
}
```
