## Finding [1](#0-0) 

### Title
Unauthenticated bandwidth-purchase flooding lets any payer evict another app's already-paid subscriptions - (File: `modules/pallets/bandwidth/src/lib.rs`)

### Summary
The bandwidth-purchase handler in `pallet-bandwidth` appends every incoming purchase to a global, per-`(app_chain, app)` FIFO list capped at 1024 entries. Once the cap is hit, the oldest entry — regardless of who paid for it or how many bytes remain on it — is silently evicted and its `remaining_bytes` are permanently lost [2](#0-1) . Because the credit destination `(chain, app)` is taken from attacker-controlled purchase-message fields rather than from `request.source`, any unprivileged buyer on any registered source chain can target an arbitrary victim app and repeatedly purchase the cheapest tier to push the victim's still-unconsumed, already-paid subscriptions out of the list.

### Finding Description
`Pallet::on_accept` decodes a `PurchaseMessage` from any request whose `request.from` matches the registered `BandwidthManager` for `request.source` [3](#0-2) . The subscription is keyed by `msg.chain` and `msg.app`, both of which come from the purchase payload itself, not from any identity tied to the caller — this is the intentional "sponsorship" feature documented in `docs/content/developers/evm/bandwidth/overview.mdx` ("a buyer on Ethereum can credit an app on Base"). Any account able to call `BandwidthManager.sol::purchase()` can therefore push new `Subscription` rows onto *any* `(app_chain, app)` bucket for the price of the cheapest tier.

`push_subscription` enforces a hard cap of `MAX_SUBSCRIPTIONS = 1024` per bucket. When the list is already full, the *oldest* entry is unconditionally evicted via `list.remove(0)` before the new entry is pushed [4](#0-3) [5](#0-4) . There is no check on who owns the evicted entry, how much `remaining_bytes` it still has, or what tier it was — a fully-loaded, expensive, mostly-unused `TierFour` subscription belonging to a legitimate app can be evicted by an attacker's cheap `TierOne` purchases. The event `SubscriptionEvicted` is emitted, but emission does not prevent the loss — the docs themselves acknowledge the FIFO drain order and eviction mechanics as reachable ("Pushing onto a full list ... evicts the oldest entry").

This mirrors the reported `Teleportation.sol` bug-class: a shared, capped resource (`maxTransferAmountPerDay` there, the 1024-slot FIFO subscription list here) can be exhausted/manipulated by any unprivileged, fee-paying caller to damage another user's position, and the guard that exists (fee vs. no fee in the original; the "no mutation on insufficient" gate here) does not address this specific griefing path because the eviction happens on the *purchase* path, not the *consume* path.

### Impact Explanation
A successful flood causes permanent loss of already-paid-for bandwidth allowance belonging to a targeted app — `remaining_bytes` on the evicted `Subscription` simply vanish, with no refund mechanism (`SubscriptionEvicted` is informational only). Since bandwidth is metered value purchased with real fee tokens via `BandwidthManager.sol`, this is a direct loss-of-funds/loss-of-purchased-service impact against the victim app, reachable purely through the public, unprivileged `purchase()` entrypoint — no relayer, prover, or admin compromise required.

### Likelihood Explanation
Any account can call `purchase()` on `BandwidthManager.sol` for the cheapest configured tier and target an arbitrary `(app_chain, app)` pair, since the credit destination is taken from the payload, not `msg.sender`'s own app identity. Reaching the 1024-entry cap requires enough purchases to push out the victim's older entries, which costs real (but bounded and attacker-controllable) fee-token spend — the same "well-funded but unprivileged attacker" profile as the original Boba report, where the attacker needed to hold `maxTransferAmountPerDay` worth of tokens. The team's own documentation flags eviction as a known, reachable mechanic under "pathological repeat-buy behavior," indicating the path is real but assumed rare under normal usage patterns — it is not gated against a deliberate, targeted flood.

### Recommendation
Scope the FIFO cap and eviction policy per payer or per minimum-remaining-value rather than a single shared per-`(app_chain, app)` list, or refuse to evict subscriptions with a `remaining_bytes`/`expires_at` value above a governance-set floor. Alternatively, protect high-value subscriptions from eviction by cheaper ones (e.g., evict by "least remaining value" rather than strict insertion order), or increase `MAX_SUBSCRIPTIONS`/require per-app admin allowlisting of which chains may sponsor credits to reduce the griefing surface.

### Proof of Concept
1. Victim app `A` on `app_chain = C` legitimately purchases a `TierFour` subscription (8 MB, high price) via `BandwidthManager.sol::purchase()` — this appends one `Subscription` to `Allowance::<T>::get(C, A)`.
2. Attacker calls `BandwidthManager.sol::purchase()` from any registered source chain, setting the purchase payload's `chain = C`, `app = A`, `tier = TierOne` (cheapest), repeating until the `(C, A)` list length reaches `MAX_SUBSCRIPTIONS` (1024) and one more push occurs.
3. Each purchase reaches `Pallet::on_accept` → `push_subscription(&msg.chain, &key, ...)` [6](#0-5) ; once the cap is exceeded, `list.remove(0)` evicts the oldest entry — eventually the victim's `TierFour` row — emitting `SubscriptionEvicted { lost_bytes: <victim's remaining bytes> }` [7](#0-6) .
4. Victim's paid-for, unconsumed bandwidth is permanently gone; subsequent dispatches from app `A` fail the gate (`GateError::NoAllowance`/`Insufficient`) until app `A` repurchases, at the cost the attacker inflicted.

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
