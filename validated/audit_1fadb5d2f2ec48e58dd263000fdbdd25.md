<br>

### Title
Unprivileged bandwidth-purchase spam evicts a victim app's paid subscriptions with no refund - (File: `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`pallet-bandwidth` credits prepaid bandwidth to an `(app_chain, app)` key taken directly from the purchase payload rather than from the caller's own identity, and caps each app's subscription list at `MAX_SUBSCRIPTIONS` (1024), silently evicting the oldest entry with no compensation once the cap is hit. Because any account can call `purchase()` on `BandwidthManager.sol` and set an arbitrary `app` key/chain in the message body, an unprivileged attacker can cheaply spam purchases against a victim app to push its legitimately-purchased, unconsumed subscription(s) out of the FIFO list, permanently destroying the bytes the victim already paid for — the same "paid-for value silently stranded/lost" invariant break as the `InfraredVault`/`MultiRewards` report, but reachable here by an ordinary user rather than requiring an idle period.

### Finding Description
`on_accept` decodes an inbound purchase message and calls `push_subscription`, which is keyed purely by `msg.chain` and `msg.app` from the message body: [1](#0-0) 

`push_subscription` appends to a `BoundedVec` capped at `MAX_SUBSCRIPTIONS`; when full, it unconditionally pops index 0 (the oldest, FIFO-ordered) entry and drops its `remaining_bytes`, only emitting an audit event — there is no repayment or protection for the evicted subscription's owner: [2](#0-1) 

As the docs confirm, the allowance key is intentionally decoupled from the caller/source of the message ("keyed by `(app_chain, app)` taken from the message body, not by `request.source`") to support cross-chain sponsorship — but this same design means *anyone* can target *any* app's allowance row with a purchase, since the only authentication check is that the message came from the registered `BandwidthManager` contract for that source chain, not that the payer is related to `app`: [3](#0-2) 

The gate itself (`try_consume`) drains subscriptions in the same FIFO order the eviction respects, so whichever subscription is oldest is both the first drained by legitimate traffic and the first destroyed by eviction spam: [4](#0-3) 

This is structurally the same broken invariant as the external report: a resource that was fully paid for (`remaining_bytes` in a `Subscription`, analogous to accrued but un-earned `rewardPerToken` in `MultiRewards`) is destroyed by a code path that has no notion of "this value is still owed to someone" — except here the destructive path (`push_subscription`'s eviction) is directly and cheaply triggerable by any unprivileged caller against an arbitrary victim, not merely a passive side effect of an idle period.

### Impact Explanation
An attacker can grief any `(app_chain, app)` pair by repeatedly purchasing the cheapest configured tier (1 month, minimal `bytes`) 1024 times, each purchase costing only the governance-set cheapest tier price. Every purchase evicts the oldest live subscription. If the victim previously bought a high-value tier (large `bytes` × many `months`, i.e. a large prepaid balance with a long expiry), that entire prepaid balance is destroyed for the cost of the attacker's cheap spam — a direct, disproportionate loss of prepaid funds/bandwidth for the victim app, with the lost value only recorded via `SubscriptionEvicted` and never refunded. This is a straightforward "loss of funds via logic attack" reachable by any unprivileged account with a wallet on the source chain.

### Likelihood Explanation
Likelihood is high wherever `pallet-bandwidth` is deployed with any tiers configured: the attacker needs no special permissions, no relayer/prover collusion, and no admin access — only the ability to call the permissionless `purchase()` entrypoint on the registered `BandwidthManager` for the target's source chain 1024 times, which is bounded purely by the cheapest tier's price and gas. The economic cost scales with the cheapest tier price, while the damage scales with whatever the victim previously paid for its largest subscription, making the attack profitable/asymmetric whenever tier pricing isn't uniform.

### Recommendation
Do not let an unauthenticated purchase evict another party's unexpired subscription without compensation. Options: (1) key subscriptions (or at least eviction eligibility) by the paying account/relationship rather than purely by `(app_chain, app)`, so unrelated purchasers can't evict each other's credits; (2) evict by remaining value/expiry rather than strict insertion order (e.g., only evict subscriptions closest to expiry, or require the evicted entry's value to be below a floor); (3) increase `MAX_SUBSCRIPTIONS` or otherwise decouple the cap from a single shared list per app so cheap spam can't dominate the FIFO slots; (4) refund/redirect the evicted subscription's remaining bytes to a claimable balance instead of silently discarding them.

### Proof of Concept
1. Governance configures two tiers: `Tier::Cheap` (`bytes = 1`, `duration_secs = 1`, minimal price) and `Tier::Enterprise` (`bytes = 1_000_000_000`, `duration_secs = 31536000`, high price).
2. Victim buys `Tier::Enterprise` once for `app = "VictimApp"`, `chain = EVM-1` via `BandwidthManager.purchase()`, crediting one `Subscription` with `remaining_bytes = 1_000_000_000` at index 0 of the FIFO list — see `on_accept`/`push_subscription` at `modules/pallets/bandwidth/src/lib.rs:467-486` and `:400-437`.
3. Attacker calls `BandwidthManager.purchase()` 1024 times with `tier = Cheap`, `months = 1`, setting `app = "VictimApp"` and `chain = EVM-1` in the payload each time (nothing prevents an unrelated caller from targeting another app's key, per `modules/pallets/bandwidth/src/lib.rs:441-486` and the docs' explicit "keyed by message body, not `request.source`" note).
4. After the 1025th total credit to `("VictimApp", EVM-1)`, `push_subscription` evicts index 0 — the victim's `Tier::Enterprise` subscription with `1_000_000_000` remaining bytes — emitting `SubscriptionEvicted { lost_bytes: 1_000_000_000 }`, and the victim's paid bandwidth is permanently gone with no refund path (`modules/pallets/bandwidth/src/lib.rs:416-425`).

### Citations

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

**File:** modules/pallets/bandwidth/src/lib.rs (L467-486)
```rust
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
```

**File:** modules/pallets/bandwidth/src/lib.rs (L523-555)
```rust
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
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L110-114)
```text
The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
```
