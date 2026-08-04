## Analog Found: Cheap-Purchase Flood Evicts Paid Bandwidth in `pallet-bandwidth`

The external Ditto report's core primitive — an attacker flooding a shared, capacity-bound, FIFO-like structure with cheap entries to corrupt honest usage of that structure — has a direct, fund-loss analog in Hyperbridge's bandwidth subscription ledger.

### Title
Unrestricted `purchase()` sponsorship + strict-insertion-order eviction lets an attacker destroy any app's paid, unconsumed bandwidth for the cost of 1024 cheap purchases - (File: `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`pallet-bandwidth`'s `Allowance` storage keeps a FIFO `BoundedVec<Subscription, 1024>` per `(app_chain, app)`. Any `BandwidthManager.sol` purchase, by design, is allowed to credit *any* `app`/`chain` pair — this is the documented "sponsorship" feature (a payer on one chain can pay for any app on any chain). When the list is at capacity, `push_subscription` unconditionally evicts `list.remove(0)` — the *oldest inserted* subscription — regardless of whether it still has `remaining_bytes` left or how much it cost. An attacker can therefore buy the cheapest configured tier 1024 times for a victim's `(app_chain, app)` key, evicting the victim's legitimately-purchased, unconsumed, high-value subscriptions purely because they were inserted earlier.

### Finding Description
`push_subscription` in `modules/pallets/bandwidth/src/lib.rs`: [1](#0-0) 

evicts strictly by insertion order (`list.remove(0)`), not by remaining value, expiry, or tier size. `on_accept` (the ISMP message handler invoked when any registered `BandwidthManager` purchase message lands) calls `push_subscription` for the `(msg.chain, msg.app)` pair taken straight from the purchase payload: [2](#0-1) 

Crucially, `msg.chain`/`msg.app` are **not tied to the caller** — this is intentional sponsorship, documented explicitly: [3](#0-2) [4](#0-3) 

So anyone can call `BandwidthManager.purchase()` on any configured source chain, targeting any victim `app` on any `app_chain`, at the cheapest configured tier. Doing this 1024 times evicts every prior subscription for that `(app_chain, app)` key — including large, recently-purchased, mostly-unconsumed subscriptions the victim paid real money for — well before their `remaining_bytes` are drained or their `expires_at` is reached. This mirrors the Ditto bug's invariant break exactly: a cheap, self-serving flood of entries into a shared bounded structure denies/destroys the intended use of that structure for everyone else, except here the corrupted value is concrete on-chain state (`Allowance` list contents) representing already-paid funds, not just gas.

### Impact Explanation
This is a direct loss-of-funds/paid-resource vulnerability: the victim's paid bandwidth (`remaining_bytes` in the evicted `Subscription`) is destroyed by a third party who pays only for 1024 minimum-tier purchases — an asymmetric griefing/fund-destruction primitive. Since bandwidth gating (`BandwidthGate::try_consume`) governs whether an app's cross-chain messages/GET responses are even processed, victims whose subscriptions are evicted lose the paid capacity to have their messages relayed, which can stall or brick an app's cross-chain operation until it repurchases. `SubscriptionEvicted` even self-documents `lost_bytes` as an auditable loss: [5](#0-4) 

### Likelihood Explanation
The attack requires no privileged role, no relayer/prover/admin compromise, and no malicious peer assumption — it's a fully permissionless, unprivileged public entrypoint (`BandwidthManager.purchase()` → ISMP dispatch → `pallet-bandwidth::on_accept`). The only cost is 1024 × cheapest-tier price, which governance may set arbitrarily low, and the payoff (destroying a victim's much larger/expensive subscription) can vastly exceed that cost. The eviction policy comment even acknowledges the cap ("SubscriptionEvicted ... lost_bytes ... user paid for and won't get to use") without any protection against adversarial, targeted flooding.

### Recommendation
- Evict based on remaining value/least economic loss (e.g., pick the subscription with the smallest `remaining_bytes × time-to-expiry` or reject pushes when the list is full instead of silently evicting live, valuable entries).
- Consider capping or rate-limiting purchases credited to a given `(app_chain, app)` per source/caller, or requiring `msg.sender`/`request.from`-based bucketing so sponsorship flooding of a *third-party* app is constrained.
- Alternatively, only allow eviction of already-expired or fully-drained entries; if none qualify, reject the purchase (or grow storage) rather than destroying live paid value.

### Proof of Concept
1. Governance configures `TierIndex::TierOne` with minimal `bytes`/`duration_secs` and a low EVM-side price via `set_tier` + `dispatch_set_tiers`.
2. Victim's app (`app_chain = X`, `app = victimApp`) has a handful of high-value subscriptions purchased legitimately (large `bytes`, long `duration_secs`), none yet expired or drained.
3. Attacker calls `BandwidthManager.purchase({app: victimApp, tier: TierOne, months: 1, chain: X})` 1024 times (any EOA, any chain with a registered manager) — no relationship to `victimApp` required, per the sponsorship design.
4. Each purchase message reaches `pallet-bandwidth::on_accept` → `push_subscription`, which once `Allowance::<T>::get(X, victimApp).len() == 1024` starts calling `list.remove(0)`, evicting the victim's oldest — i.e., likely still-live and high-value — subscriptions and emitting `SubscriptionEvicted { lost_bytes }` for each.
5. After 1024 attacker purchases, all of the victim's pre-existing subscriptions are gone; the victim has to notice via `SubscriptionEvicted` events and repurchase, while the attacker has destroyed value at a fraction of the cost.

### Citations

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

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L108-114)
```text
## Sponsorship

The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
```

**File:** docs/content/developers/evm/bandwidth/purchasing.mdx (L192-205)
```text
## Sponsoring Another Chain

The `chain` argument is **not validated against the source chain** — see [Overview → Sponsorship](/developers/evm/bandwidth/overview#sponsorship) for the model. A buyer on Ethereum credits an app on Base by passing the credit chain id in `chain`:

```solidity
manager.purchase({
    app:    abi.encodePacked(appAddressOnBase),
    tier:   2,
    months: 6,
    chain:  bytes("EVM-8453")
});
```

The pallet keys allowance storage on `(msg.chain, msg.app)`, so the credit lands on Base regardless of which chain sent the payment. The recommended pattern for teams running a central treasury is to deploy `BandwidthManager` on one low-fee chain and sponsor bandwidth for app instances elsewhere.
```
