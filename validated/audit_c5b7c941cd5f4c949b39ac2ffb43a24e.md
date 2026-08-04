### Title
Unrestricted third-party bandwidth sponsorship allows griefing-eviction of another payer's prepaid subscription - ([File: modules/pallets/bandwidth/src/lib.rs])

### Summary
`pallet-bandwidth`'s subscription ledger is a per-`(app_chain, app)` FIFO list capped at `MAX_SUBSCRIPTIONS` (1024). Any account on any registered source chain can call `purchase()` and credit bandwidth to *any* `(chain, app)` pair — the pallet does not tie the credited bucket to the caller's own identity, by design, to support cross-chain sponsorship. Because pushes past the cap silently evict the **oldest** entry regardless of its remaining value, an unprivileged third party can flood cheap purchases against a victim app's bucket to force-evict a legitimate sponsor's larger, still-unspent, already-paid-for subscription before it is consumed.

### Finding Description
`Allowance` is a `StorageDoubleMap<StateMachine, AppKey, SubscriptionList>` where `SubscriptionList` is a `BoundedVec<Subscription, MaxSubscriptions>` (cap 1024) [1](#0-0) . Credits are keyed by `app_chain`/`app` taken from the purchase message body, **not** by `request.source`, explicitly so that "a treasury on a single chain can sponsor bandwidth for an app deployed across many chains" [2](#0-1) . The only authentication performed in `on_accept` is that the purchase message came from the registered `BandwidthManager` contract on its source chain — the `app`/`chain` fields inside the message are fully attacker-controlled and unrelated to the caller's own funds or identity [3](#0-2) .

`push_subscription` appends every successful purchase to the target `(app_chain, app)` list; once the list is at the 1024 cap, the next push unconditionally removes `list[0]` — the oldest entry — regardless of how much of it is unspent, and only emits an event; it never checks value or ownership before evicting: [4](#0-3) 

Since `push_subscription` is invoked unconditionally from `on_accept` for any accepted purchase [5](#0-4) , an attacker who is not the original sponsor and holds no relationship to the victim app can:
1. Observe (all of this is public on-chain state) that a legitimate sponsor purchased a large, long-duration tier for app `A` on chain `C`.
2. Issue repeated cheap `purchase()` calls (any tier, `months = 1`) targeting the same `(C, A)` bucket until the list reaches the 1024 cap.
3. Push one more purchase, which evicts the victim's still-unspent (and possibly much larger/expensive) subscription entry — permanently losing the bytes the legitimate sponsor already paid for, before the app ever had a chance to consume them.

The "no mutation on insufficient" and atomic-drain properties of the gate (`BandwidthGate::try_consume`) protect against races during *consumption* [6](#0-5) , but nothing protects the FIFO *list itself* against griefing insertions from unrelated parties, because eviction is purely insertion-order based and identity-agnostic.

### Impact Explanation
This is a direct loss of prepaid funds for the rightful sponsor: bytes that were purchased and paid for (in the fee token, transferred to the `BandwidthManager` contract) are evicted from the ledger and can never be consumed, with the loss size proportional to whatever remained on the victim's subscription at eviction time (up to the full purchased amount, e.g. a full $1000/8MB tier). The victim has no way to protect their subscription — the sponsorship feature intentionally allows any third party to insert into any app's bucket, and the eviction path performs no value or ownership comparison.

### Likelihood Explanation
Likelihood is Medium: the attacker needs no privileged role, key, or relayer/prover collusion — a plain call to `BandwidthManager.purchase()` (or the pallet's `on_accept` inbound path) with a chosen `chain`/`app` and the cheapest configured tier is sufficient, and the attack is fully deterministic given knowledge of the victim's subscription and the current queue depth. The main cost is the sum of the cheapest tier price times however many purchases are needed to reach the cap (which shrinks sharply if the bucket already has entries near the cap, or if a smaller/near-empty bucket is targeted right after a large sponsor purchase).

### Recommendation
Do not evict purely by insertion order irrespective of caller identity or value. Options:
- Track the payer/sponsor per subscription and require eviction to prefer entries from the same payer, or disallow eviction of another payer's unexpired, high-remaining-value entries.
- Replace the hard FIFO cap with per-payer sub-limits, or increase/require an eviction fee/minimum age before an entry becomes evictable, so a fresh flood cannot immediately displace an old, valuable entry.
- Alternatively, restrict which `(source, app)` pairs a given `BandwidthManager` instance may credit (e.g., require the caller's `msg.sender` on the source chain to match `app`, and gate the free "sponsorship" cross-chain crediting behind an explicit allowlist configured by the target app/governance) rather than leaving `app`/`chain` fully attacker-chosen.

### Proof of Concept
1. Governance registers a `BandwidthManager` on chain `C` and configures `TierOne` (cheap) and `TierFour` (expensive/large) [7](#0-6) .
2. A legitimate sponsor calls `purchase(app=A, tier=TierFour, months=12, chain=C)`, paying a large sum; `on_accept` appends this as `Allowance[C][A][k]` with a large `remaining_bytes` and long `expires_at` [5](#0-4) .
3. Attacker (unrelated party) calls `purchase(app=A, tier=TierOne, months=1, chain=C)` `1024 - k` additional times in quick succession, filling `Allowance[C][A]` to the 1024 cap.
4. Attacker issues one more `purchase()` against `(C, A)`; `push_subscription` evicts `list[0]` — the legitimate sponsor's entry from step 2 if it is still the oldest live entry — emitting `SubscriptionEvicted { app_chain: C, app: A, tier: TierFour, lost_bytes: <large> }` [8](#0-7) .
5. The legitimate sponsor's prepaid, unspent, unexpired bandwidth is now permanently gone, and app `A` on chain `C` can no longer use it — a direct loss of funds caused entirely by an unprivileged third party's public, permissionless calls.

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L99-130)
```rust
	/// Authorised purchase contract per source chain. A purchase whose
	/// `request.from` doesn't match this is rejected.
	#[pallet::storage]
	pub type BandwidthManager<T: Config> =
		StorageMap<_, Twox64Concat, StateMachine, H160, OptionQuery>;

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

	/// Apps that bypass the gate. Used during phased rollout for
	/// protocol-sponsored apps that haven't migrated.
	#[pallet::storage]
	pub type Allowlist<T: Config> =
		StorageDoubleMap<_, Twox64Concat, StateMachine, Blake2_128Concat, AppKey, (), OptionQuery>;

	/// Active tier SKUs keyed by `TierIndex`. Absent (or `None` via
	/// `set_tier`) means the tier is unconfigured; purchases against
	/// it are rejected.
	#[pallet::storage]
	pub type Tiers<T: Config> = StorageMap<_, Twox64Concat, TierIndex, TierConfig, OptionQuery>;
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

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L108-114)
```text
## Sponsorship

The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
```
