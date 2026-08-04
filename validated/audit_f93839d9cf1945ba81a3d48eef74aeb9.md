### Title
Unprivileged bandwidth-sponsorship purchases can evict a victim's live, paid subscription from the FIFO queue with no minimum-remaining-bytes protection — ([File: modules/pallets/bandwidth/src/lib.rs])

### Summary
`pallet-bandwidth`'s subscription ledger is a per-`(app_chain, app)` FIFO list capped at 1024 entries. Any caller can credit *any* app's bucket by dispatching a purchase from a registered `BandwidthManager` — the pallet only checks that the message came from the registered manager contract, not that the buyer is related to the `app` being credited (this is the documented "sponsorship" feature). When the list is full, pushing a new subscription unconditionally evicts index 0 — the oldest entry — regardless of how many bytes remain on it.

### Finding Description
`Pallet::push_subscription` [1](#0-0)  evicts the oldest subscription once `Allowance` for a `(app_chain, app)` key reaches `MAX_SUBSCRIPTIONS` (1024), with no check on the evicted entry's `remaining_bytes`:

```rust
let evicted = Allowance::<T>::mutate(app_chain, app, |list| {
    let evicted = if list.len() == MAX_SUBSCRIPTIONS as usize {
        Some(list.remove(0))
    } else { None };
    let _ = list.try_push(new_sub);
    evicted
});
```

This is invoked from `on_accept` for every inbound purchase message [2](#0-1) , which authenticates only that `request.from == manager` for the source chain — it never checks that `msg.app` (the credited app) belongs to, or was authorized by, `msg.sender`/`request.from`'s actual caller. Combined with the documented sponsorship model ("a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`" — [3](#0-2) ), this means **any unprivileged address can target a victim app's bucket** and push cheap purchases until the queue reaches its 1024 cap, then continue pushing to evict the victim's legitimate, unconsumed subscription(s) — including a large-`remaining_bytes` one purchased at a premium tier — with zero refund and no protocol-level minimum-remaining protection, exactly analogous to the referenced `MINIMUM_LIQUIDITY` bug where a large withdrawal is allowed to push a shared balance below a floor that other participants depend on.

### Impact Explanation
A victim app that has paid for bandwidth (its cross-chain messaging allowance) can have that paid-for, unexpired allowance permanently destroyed by a third party with no interaction from or consent by the victim, and with no compensation — this is a direct loss of already-paid funds/entitlement enforced by `BandwidthGate::try_consume` [4](#0-3) , which will subsequently reject the victim's legitimate ISMP dispatches (`GateError::NoAllowance`/`Insufficient`) once its subscriptions are wiped. This is a "logic attack" causing loss/destruction of paid protocol resources for the rightful beneficiary, which the eviction event `SubscriptionEvicted` only makes auditable after the fact — it does not prevent it.

### Likelihood Explanation
The attack requires no privileged role, relayer, or governance access — only funding 1024+ cheap purchases against the victim's `(app_chain, app)` key via a registered `BandwidthManager.purchase()` call (any external account can call `purchase()`). Cost scales with the price of the cheapest configured tier × (1024 + number of victim entries to evict), which the docs themselves flag as the only thing standing between "pathological repeat-buy behavior" and normal use ("at the default of one purchase per cycle, 1024 buys is years of headroom" — [5](#0-4) ), implicitly acknowledging the eviction path is reachable by a non-privileged buyer without any additional authorization check tying `app` to a specific payer.

### Recommendation
Before evicting on a full FIFO list, require that the evicted entry either has already expired or has `remaining_bytes` below some acceptable/near-zero threshold, and reject/queue the new purchase (or refund/redirect it) rather than silently destroying a live, materially-funded subscription — mirroring the `MINIMUM_LIQUIDITY`-style fix of checking the post-operation invariant (`remaining_bytes` of the entry being evicted, and/or total remaining bytes for the bucket) before allowing the mutation to proceed. Alternatively, scope sponsorship so that evictions can only remove entries whose `purchased_at`/`expires_at` show they were already near-exhausted, or add a governance-configurable minimum "protected" byte threshold per bucket that eviction cannot cross.

### Proof of Concept
1. Victim's app (`app_chain = EVM-8453`, `app = V`) buys a large Tier4 subscription via `BandwidthManager.purchase()`: `Allowance[(EVM-8453, V)]` now holds `[{tier: 4, remaining_bytes: 8_000_000, expires_at: now+90d}]`.
2. Attacker (any address, on any chain with a registered `BandwidthManager`) repeatedly calls `purchase(app = V, chain = EVM-8453, tier = cheapest, months = 1)` targeting the same `(EVM-8453, V)` bucket — this is fully permitted, per the sponsorship model documented in `docs/content/developers/evm/bandwidth/overview.mdx`.
3. After 1023 attacker purchases, the bucket for `V` holds 1024 entries (the victim's entry still at index 0, since FIFO is insertion-ordered — [6](#0-5) ).
4. Attacker's 1024th purchase triggers `push_subscription`, which evicts index 0 — the victim's 8,000,000-byte subscription with 90 days left — emitting `SubscriptionEvicted { lost_bytes: 8_000_000 }`, and the victim receives nothing.
5. `BandwidthGate::try_consume` for `V`'s subsequent dispatches now only sees the attacker's near-empty cheap-tier subscriptions and quickly returns `GateError::Insufficient`/`NoAllowance`, blocking `V`'s legitimate cross-chain traffic despite having paid for a large, unexpired allowance.

Note: I was unable to independently verify the exact current price of the cheapest tier or whether any additional off-chain economic assumption (e.g., extremely high tier floor pricing) makes this specific attack cost-prohibitive in practice — that would require live/governance-configured `Tiers` values not visible in the indexed code, and should be checked against the deployed configuration.

### Citations

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

**File:** modules/pallets/bandwidth/src/lib.rs (L509-564)
```rust
impl<T: Config> BandwidthGate for Pallet<T> {
	fn try_consume(
		source: &ismp::host::StateMachine,
		app: &[u8],
		bytes: u32,
	) -> Result<(), GateError> {
		let key = AppKey::truncate_from(app.to_vec());
		if Allowlist::<T>::contains_key(source, &key) {
			return Ok(());
		}

		let need: u128 = bytes.into();
		let now = <T as pallet_ismp::Config>::TimestampProvider::now().as_secs();

		let total = pallet::Allowance::<T>::mutate(source, &key, |list| {
			// Sweep expired in-place. Order-preserving.
			list.retain(|s| s.expires_at > now);

			if list.is_empty() {
				return Err(GateError::NoAllowance);
			}

			let total: u128 = list.iter().map(|s| s.remaining_bytes).sum();
			if total < need {
				return Err(GateError::Insufficient { remaining: total, required: need });
			}

			// Drain from the front in insertion order. Once a sub is
			// fully consumed, pop it and continue with the next.
			// `get_mut` defends against a malformed list that satisfies
			// the `total >= need` precheck but is structurally empty;
			// we'd otherwise panic via `list[0]`.
			let mut left = need;
			while left > 0 {
				let Some(head) = list.get_mut(0) else {
					return Err(GateError::NoAllowance);
				};
				let take = head.remaining_bytes.min(left);
				head.remaining_bytes = head.remaining_bytes.saturating_sub(take);
				left = left.saturating_sub(take);
				if head.remaining_bytes == 0 {
					list.remove(0);
				}
			}

			Ok(total)
		})?;

		Self::deposit_event(Event::BandwidthConsumed {
			source: *source,
			app: key,
			bytes: need,
			remaining: total - need,
		});
		Ok(())
	}
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L73-77)
```text
This matters when you queue multiple tiers: the cheapest/oldest entry is consumed first regardless of which tier it came from. Plan top-ups so a higher tier doesn't sit behind a soon-to-expire lower tier you'd rather burn last.

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
