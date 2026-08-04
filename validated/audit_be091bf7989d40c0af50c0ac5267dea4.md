### Title
Permissionless `purchase()` allows an attacker to force-evict a victim app's already-paid, unconsumed bandwidth allowance - (File: `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`pallet-bandwidth` stores each `(app_chain, app)` subscription queue as a `BoundedVec` capped at `MAX_SUBSCRIPTIONS = 1024`. When the cap is reached, a new purchase unconditionally evicts the **oldest** entry regardless of how many `remaining_bytes` (i.e., how much already-paid value) it still holds. The `purchase()` flow is explicitly permissionless with respect to which `app` receives the credit — any caller can dispatch a `BandwidthPurchaseMsg{app, tier, months, chain}` naming an arbitrary victim `app`. This mirrors the StRSR bug class: a threshold-crossing event (queue reaching capacity) triggers unconditional destruction of accumulated value, and the trigger can be manufactured by a small/cheap external action even though the value at risk (the victim's unconsumed subscriptions) can be large.

### Finding Description
The FIFO queue and eviction rule are documented at [1](#0-0) , and the eviction event explicitly acknowledges the loss: `SubscriptionEvicted { ... lost_bytes }` where "`lost_bytes` is what the user paid for and won't get to use" [2](#0-1) .

Crediting happens in `on_accept`, which derives the target `(app_chain, app)` key straight from the untrusted purchase message body — not from `request.from` or any binding to the payer's own identity: [3](#0-2) 

The only authentication check is that `request.from` matches the registered `BandwidthManager` contract for the *source chain* [4](#0-3) ; there is no check that the caller of `purchase()` on that manager is affiliated with the `app` being credited. This is a documented, intentional multi-tenant "sponsorship" feature: "a treasury on a single chain can sponsor bandwidth for an app deployed across many chains" and "a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = ...`" [5](#0-4) .

Because the eviction policy pops strictly from the head (oldest) of the list on overflow, and insertion order determines position, any victim's *earlier* (and potentially large/expensive) subscriptions sit at the head. An attacker who repeatedly calls `purchase()` naming the victim's `app` with the cheapest tier can:
1. Fill the remaining slack in the victim's 1024-entry queue.
2. Once full, each additional purchase evicts the queue head — i.e., the victim's own oldest, still-unconsumed, already-paid-for subscription — wiping `lost_bytes` of value that the victim paid real fee-token for.

This is the exact analog of the StRSR issue: the "threshold" is `list.len() == MAX_SUBSCRIPTIONS`, and crossing it destroys a specific unit of state (a subscription) without regard to how much value it still represents, and the crossing can be manufactured cheaply by an unprivileged party who need not compromise any relayer, prover, or admin.

### Impact Explanation
A victim app that has paid for a large, long-duration tier (e.g., the $1000/8MB plan) can have that entire prepaid allowance permanently destroyed by an attacker who spends comparatively little to spam cheap tier purchases into the same `(app_chain, app)` queue, since eviction is chosen purely by FIFO position and not by remaining value. This is a direct loss of funds for the victim (prepaid, non-refundable bandwidth that is wiped before use) triggered by an unauthenticated, unprivileged third party — matching the bounty's "stealing or loss of funds" / "logic attack" impact classes.

### Likelihood Explanation
The attack requires no relayer, prover, governance, or admin compromise — only calling the public `purchase()` function on `BandwidthManager.sol` from any EOA, targeting an arbitrary `app` value in the message body, and paying the tier price for each spam purchase. The cost scales with `1024 - (victim's current queue length)`, using the cheapest available tier ($50/100KB), making the attack economically bounded but fully permissionless and always available as long as any `BandwidthManager`/tier is configured. The eviction and its loss are even called out as expected behavior in the code comments and docs, indicating the destructive threshold-crossing was accepted as a design tradeoff rather than mitigated against a hostile third party deliberately targeting another app's queue.

### Recommendation
- Restrict which `app` a purchase message may credit to the caller's own identity (e.g., derive `app` from `request.from`/`msg.sender` context rather than trusting an attacker-supplied field), or require an explicit opt-in/allowlist from the app before third-party sponsorship can top up its queue.
- Change the eviction policy so it does not unconditionally destroy the oldest entry: e.g., evict entries by remaining value/expiry proximity, merge same-tier entries instead of queueing, or reject the purchase (refund) if it would evict a subscription that still has significant unconsumed value, analogous to the "manual governance push" mitigation used for StRSR — require an explicit privileged/governance action to force an eviction of a non-trivial subscription rather than allowing it as a side effect of any permissionless purchase.

### Proof of Concept
1. Victim app `V` (on `app_chain = EVM-8453`) purchases the $1000 tier (8MB, long duration) once — the subscription is inserted as `Allowance[EVM-8453][V][0]`.
2. Attacker, from any address on any registered source chain, calls `BandwidthManager.purchase()` `1024` times with `app = V`'s key and `months = 1` on the cheapest `$50` tier, each dispatching a `BandwidthPurchaseMsg{app: V, tier: TierOne, months: 1, chain: EVM-8453}`.
3. Each message is accepted by `on_accept` (only `request.from == manager` is checked, per [4](#0-3) ) and appended via `push_subscription`.
4. Once `Allowance[EVM-8453][V]` reaches 1024 entries, the next appended purchase evicts index `0` — the victim's original 8MB subscription — emitting `SubscriptionEvicted{ app_chain: EVM-8453, app: V, tier: TierFour, lost_bytes: 8_000_000×months }` [2](#0-1) .
5. Victim `V` has permanently lost the entire 8MB allowance it already paid for, with no recourse, despite never having consumed any of it — analogous to stakers losing all holdings in StRSR when a new era begins despite significant value remaining.

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L22-30)
```rust
//! Each `(chain, app)` row holds a FIFO list of [`Subscription`]s
//! (`BoundedVec`, capped at 1024). Every purchase appends a new
//! subscription with a fixed `expires_at`; expiry never extends and
//! same-tier repurchases don't stack — they queue. The gate drains
//! the oldest live subscription first; once empty it moves to the
//! next. Subscriptions that aren't reached before their expiry are
//! swept silently — what you paid for is yours only until it expires.
//! Pushes onto a full list evict the oldest entry and emit
//! [`Event::SubscriptionEvicted`].
```

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

**File:** modules/pallets/bandwidth/src/lib.rs (L456-465)
```rust
			let manager = BandwidthManager::<T>::get(&request.source).ok_or_else(|| {
				anyhow::anyhow!(format!("no bandwidth manager registered for {:?}", request.source))
			})?;

			if request.from != manager.0.to_vec() {
				return Err(anyhow::anyhow!(format!(
					"purchase from unauthorised sender on {:?}: expected {:x?}, got {:x?}",
					request.source, manager.0, request.from
				)));
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

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L108-114)
```text
## Sponsorship

The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
```
