### Title
Permissionless FIFO eviction of prepaid bandwidth lets an attacker destroy a victim app's already-paid subscription bytes - ([File: modules/pallets/bandwidth/src/lib.rs])

### Summary
`pallet-bandwidth` stores a per-`(app_chain, app)` FIFO list of `Subscription`s capped at `MAX_SUBSCRIPTIONS = 1024`. Any purchase — from any source chain, naming any `(chain, app)` pair via the sponsorship model — appends a new subscription, and once the list is full, the oldest entry is evicted and its unused, already-paid bytes are permanently destroyed (`SubscriptionEvicted { lost_bytes }`). Because `on_accept` credits whatever `(msg.chain, msg.app)` the purchase message specifies (not the caller's own identity), an unprivileged attacker can target any victim app and force-evict its live, high-value subscription by flooding cheap purchases against the exact same key.

### Finding Description
`push_subscription` (`modules/pallets/bandwidth/src/lib.rs:400-437`) unconditionally appends a new subscription to `Allowance::<T>` keyed by `(app_chain, app)`, and when the `BoundedVec` is already at its 1024 cap it evicts `list.remove(0)` — the oldest entry — before pushing the new one:

```rust
let evicted = Allowance::<T>::mutate(app_chain, app, |list| {
    let evicted = if list.len() == MAX_SUBSCRIPTIONS as usize {
        Some(list.remove(0))
    } else {
        None
    };
    let _ = list.try_push(new_sub);
    evicted
});
``` [1](#0-0) 

The key that gets mutated (`app_chain`, `app`) is taken directly from the purchase message body, not from the identity of the chain/contract that sent the ISMP request:

```rust
let key = AppKey::truncate_from(msg.app);
let expires_at = Self::push_subscription(&msg.chain, &key, tier, bytes, duration);
``` [2](#0-1) 

The only authentication performed is that the purchase came from *some* registered `BandwidthManager` on its source chain (`request.from == manager`) — not that the caller is the app itself: [3](#0-2) 

This is the intended "sponsorship" design — the docs explicitly state "a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`" and that the ledger is "keyed by `(app_chain, app)` taken from the message body, not by `request.source`" — this is deliberately multi-tenant-friendly. [4](#0-3) 

The eviction mechanism itself is also intentional (documented as auditable via `SubscriptionEvicted`), but the FIFO/1024-cap design combined with permissionless third-party crediting of *any* app's bucket creates the exploitable path: because tier prices range from $50/100KB up to $1000/8MB, an attacker can buy the cheapest tier (`TierOne`, $50 for 100KB) 1024 times against a victim's `(app_chain, app)` key. Each purchase appends one row; the 1025th purchase evicts the victim's oldest live subscription — which could be a large, recently purchased, high-value tier (e.g., $1000/8MB) that the victim has barely begun draining. The victim's paid-for bytes are gone; the gate is a strict "insufficient → no mutation, else drain" state machine with no way to recover evicted capacity (`BandwidthGate::try_consume`, `modules/pallets/bandwidth/src/lib.rs:509-564`).

This is the direct structural analog of the `maxContractBalance` bug: in the original report a whale fills a shared capacity slot to deny/disadvantage a victim's legitimate deposit; here an attacker fills a shared, capped FIFO slot (per-app subscription list) to force-evict a victim's already-paid, still-valid balance — except the outcome here is outright loss of funds already spent by the victim (paid bytes destroyed), not merely a reverted transaction.

### Impact Explanation
This falls squarely under "stealing or loss of funds": a victim app that pays for bandwidth (e.g., the $1000/8MB tier) can have that purchase evicted and permanently lost before it is ever consumed, at a cost to the attacker of only cheap repeated $50 purchases (1024 of them) against the same `(app_chain, app)` key. Since bandwidth gates message delivery for real cross-chain apps (the gate rejects a message once allowance is exhausted), destroying a victim's paid allowance can also stop the victim's app from dispatching messages through Hyperbridge, i.e., unauthorized denial of the app's paid-for service. The attack is fully permissionless, requires no relayer/prover/admin compromise, and works purely through the public `purchase()` entrypoint on `BandwidthManager.sol` targeting any `(chain, app)` pair.

### Likelihood Explanation
High feasibility: purchase() is a completely public, unprivileged entrypoint; the attacker only needs `1024 × TierOne price` in the fee token, and the target `app` identifier is public (it's the app's known contract address/module id used for its subscriptions). No race condition, front-running, or privileged access is needed — the attacker can simply submit 1024 sequential purchases at any time to guarantee eviction of whatever the victim currently holds, and can repeat this indefinitely to keep the victim's bucket perpetually capped at cheap tiers.

### Recommendation
- Scope self-service protections around the FIFO cap: e.g., disallow third-party purchases from evicting subscriptions the victim itself paid for, or track "at-risk" (soon-to-evict) entries and refuse to evict subscriptions with material remaining value/duration paid by a *different* payer than the evicting purchase.
- Alternatively, key eviction risk to the paying account rather than a shared FIFO, or raise the cap dynamically / merge same-tier purchases instead of queuing, so cheap spam purchases cannot displace a large legitimate purchase.
- At minimum, emit and expose a pre-purchase quote of "what will be evicted" so a sponsor purchase can be rejected/capped if it would evict subscriptions with more remaining value than it credits, and/or require sponsorship purchases to be no cheaper (in bytes-days) than the subscription they would evict.

### Proof of Concept
1. Victim (app `A` on chain `C`) purchases `TierFour` (8MB / $1000) via `BandwidthManager.purchase()`, dispatching a `BandwidthPurchaseMsg { app: A, chain: C, tier: 4, months: 1 }` to `pallet-bandwidth`. This appends `Subscription { remaining_bytes: 8MB, ... }` to `Allowance[C][A]`.
2. Attacker (any unrelated account, on any source chain with a registered `BandwidthManager`) submits 1024 sequential `purchase()` calls with `tier: 1` (TierOne, 100KB, $50) and the same `app: A, chain: C` payload — each one is a valid, ordinary purchase that `on_accept` processes identically to a legitimate one (`modules/pallets/bandwidth/src/lib.rs:455-489`).
3. Once `Allowance[C][A].len() == 1024`, each subsequent attacker purchase in `push_subscription` evicts the oldest entry via `list.remove(0)` (`modules/pallets/bandwidth/src/lib.rs:417-418`) — first the older, smaller entries, and eventually the victim's $1000 8MB subscription once it becomes the oldest surviving entry, emitting `SubscriptionEvicted { lost_bytes: <victim's unused bytes> }`.
4. The victim's paid allowance is now gone (`Allowance::remaining(C, A)` reflects the loss), and any of the victim's future ISMP dispatches through `BandwidthGate::try_consume` are rejected with `NoAllowance`/`Insufficient` until the victim repurchases — at a fraction of the attacker's outlay relative to the value destroyed. [5](#0-4)

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L400-489)
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

		/// The router uses this to skip the gate on purchases —
		/// otherwise a depleted app couldn't recharge.
		pub fn is_purchase_message(request: &PostRequest) -> bool {
			BandwidthManager::<T>::get(&request.source)
				.map(|m| request.from == m.0.to_vec())
				.unwrap_or(false)
		}
	}

	impl<T: Config> Default for Pallet<T> {
		fn default() -> Self {
			Self(PhantomData)
		}
	}

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
