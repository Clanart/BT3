## Confirmed Analog: Attacker-Griefable FIFO Eviction of Prepaid Bandwidth Subscriptions

### Title
Unprivileged spam purchases evict a victim's paid, unexpired bandwidth allowance — permanent loss of prepaid funds - ([File: modules/pallets/bandwidth/src/lib.rs])

### Summary
The core broken invariant in the external report is: an attacker can occupy a bounded, shared resource slot with junk entries, and the eviction/finalization logic has no way to distinguish "junk" from "legitimate" occupants, causing loss of value that was already paid for. In `pallet-bandwidth`, the FIFO `Allowance` list per `(app_chain, app)` is capped at `MAX_SUBSCRIPTIONS = 1024` [1](#0-0) . Any unprivileged, unauthenticated caller can trigger `push_subscription` for *any* target `(app_chain, app)` pair by calling `purchase()` on a registered `BandwidthManager` and naming an arbitrary victim app/chain in the purchase payload — there is no check that the buyer is the app owner. Once the FIFO list for a targeted victim hits the 1024-entry cap, the pallet unconditionally evicts the oldest entry, even if it still has `remaining_bytes > 0` and has not expired, and there is no rate limit or per-purchaser cap preventing this.

### Finding Description
Purchases are accepted in `Pallet::on_accept` after only two checks: (1) the manager is registered for `request.source`, and (2) `request.from` equals that registered manager's address [2](#0-1) . There is **no check on who paid** or that the caller is entitled to buy bandwidth for `msg.chain`/`msg.app`. This is corroborated by design docs: "a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`" — the credit target is fully attacker-controlled, keyed only by `(app_chain, app)` from the message body, not by the transaction sender [3](#0-2) .

`push_subscription` then appends the new subscription; if the target list is already at `MAX_SUBSCRIPTIONS`, it unconditionally removes index `0` — the oldest entry by insertion order — regardless of whether that entry still has bytes remaining or is far from expiring: [4](#0-3) 

The pallet's own doc comment acknowledges the loss is real and on-chain-auditable ("`lost_bytes` is what the user paid for and won't get to use") but assumes it "only happens under pathological repeat-buy behavior" [5](#0-4) . That assumption does not hold: nothing restricts who can push into a *victim's* queue. An attacker with no special privilege (not admin, not a relayer, not the app owner) can dispatch 1024 cheap purchase messages targeting the victim's exact `(app_chain, app)` key, filling the FIFO queue and evicting the victim's legitimate, unexpired, unconsumed subscription — before it was ever drained by the gate.

This is the direct structural analog of the reported bug class: a fixed-capacity slot (`activeProposalNow` there, the 1024-cap FIFO list here) can be filled with attacker-controlled "junk" entries that a legitimate actor cannot prevent or clear, and — unlike the original report where the impact was pure liveness DoS — here the consequence is concrete: the victim's already-paid-for byte allowance is permanently destroyed (`SubscriptionEvicted { lost_bytes }`), which is a loss-of-funds impact, not merely a DoS.

### Impact Explanation
This is loss of funds for the victim app/tenant: bytes that were purchased and paid for (in the fee token, at real cost per the tier pricing table) are evicted from storage before being consumed and before expiry, with no refund path. Because the target `(app_chain, app)` key is taken directly from attacker-supplied purchase payload data rather than derived from `msg.sender`/`request.from` of the actual payer, any address can grief any other app's bandwidth balance. This also indirectly enables a bridging-availability attack: once a victim's `Allowance` list is emptied of paid subscriptions, its outbound messages are rejected by `BandwidthGate::try_consume` with `GateError::NoAllowance` [6](#0-5) , blocking that app's legitimate cross-chain messages until it repurchases.

### Likelihood Explanation
The attack requires only funds to pay for 1024 minimal purchases (cheapest tier, 1 month) targeting the victim's `(app_chain, app)` — no relayer, prover, governance, or malicious peer role is needed, and no race condition or front-running is required; it is a straightforward sequence of permissionless `purchase()` calls. The pallet performs no per-buyer authentication of the credit target, and the eviction logic performs no "still active / still valuable" check before evicting — it evicts strictly by insertion order.

### Recommendation
- Authenticate credit-target ownership: require the purchase message to prove the buyer is authorized for the target `(app_chain, app)` (e.g., only the app itself, or a signed authorization from the app), rather than allowing an arbitrary third party to name any victim as the credit recipient.
- Change eviction policy from strict oldest-first to prefer evicting already-expired or fully-drained subscriptions first, and only fall back to oldest-live-entry eviction after those are exhausted; consider refusing/queueing new purchases instead of evicting live, unexpired entries with remaining bytes.
- Add a rate limit or minimum-purchase-value threshold per `(source, app_chain, app)` to make griefing economically prohibitive independent of the FIFO cap size.

### Proof of Concept
1. Victim `V` legitimately purchases Tier1 bandwidth for `(app_chain = EVM-8453, app = V)`, creating a `Subscription` with `remaining_bytes > 0` and `expires_at` far in the future.
2. Attacker `A` (any account, unprivileged, no relationship to `V`) calls `BandwidthManager.purchase()` 1024 times (or enough to fill the remaining queue slots up to `MAX_SUBSCRIPTIONS`), each time encoding a `BandwidthPurchaseMsg { app: V, chain: EVM-8453, tier: TierOne, months: 1 }` — i.e., naming `V`'s app/chain as the credit target while `A` pays.
3. Each message flows through `pallet-bandwidth::on_accept`, which only validates `request.from == registered_manager` (satisfied, since it's a legitimate manager contract forwarding `A`'s calls) — see `on_accept` at [2](#0-1)  — then calls `push_subscription(&V, ...)`.
4. Once `Allowance::<T>::get(EVM-8453, V).len() == 1024`, the next purchase evicts index 0 — which is `V`'s original, still-active, unexpired subscription — emitting `SubscriptionEvicted { lost_bytes: V's remaining bytes }` per [7](#0-6) .
5. `V`'s prepaid bandwidth allowance is now gone; any of `V`'s messages that rely on that allowance are rejected by the gate with `GateError::NoAllowance` until `V` repurchases, and the bytes `V` already paid for are unrecoverable.

### Citations

**File:** modules/pallets/bandwidth/src/types.rs (L19-22)
```rust
/// Hard cap on the subscription list per `(chain, app)`. Pushes
/// beyond this evict the oldest entry (FIFO).
pub const MAX_SUBSCRIPTIONS: u32 = 1024;
pub type MaxSubscriptions = ConstU32<MAX_SUBSCRIPTIONS>;
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

**File:** modules/pallets/bandwidth/src/lib.rs (L454-465)
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
```

**File:** modules/pallets/bandwidth/src/lib.rs (L509-528)
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
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L110-114)
```text
The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
```
