This confirms the full path: `msg.chain` (an attacker-controlled, arbitrary `AppKey`/`StateMachine` pair from the purchase body) directly indexes `Allowance::<T>` in `push_subscription`, with **no relationship required to `request.source` or to the caller's own app**. Combined with the FIFO eviction-on-overflow logic, this gives an unprivileged, economically-bounded but fully deterministic path to destroy a victim's paid-for bandwidth allowance.

### Title
Attacker-chosen `chain`/`app` key in bandwidth purchase lets anyone force-evict a victim's prepaid subscription via FIFO overflow - (File: `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`pallet-bandwidth`'s `on_accept` credits a new `Subscription` to `Allowance::<T>[msg.chain][msg.app]`, where `msg.chain` and `msg.app` are taken verbatim from the attacker-controlled `BandwidthPurchaseMsg` body dispatched by `BandwidthManager.purchase()`, and are **not required to match `request.source` or `msg.sender`** (this is the intentional "sponsorship" feature). `push_subscription` appends to a `BoundedVec` capped at `MAX_SUBSCRIPTIONS = 1024`, evicting `list[0]` (the chronologically oldest entry) whenever the cap is hit. Any account can therefore target any `(app_chain, app)` pair, buy the cheapest tier repeatedly, and once the queue reaches 1024 entries deterministically evict the victim's still-unconsumed, real-money-funded subscription — matching the "force a shared automated allocation into an extreme state via public deposit/withdraw-style calls" primitive from the source report (attacker manipulates shared pooled state to damage another legitimate user), just expressed as bandwidth loss instead of interest-rate manipulation.

### Finding Description
- `BandwidthManager.purchase()` lets **any caller** pay for `app`/`chain` of their choosing: [1](#0-0) 
- On Hyperbridge, `on_accept` trusts `msg.chain`/`msg.app` from the body to key storage, with the only authentication being that the *manager contract* on the source chain is registered — not that the caller owns the app/chain being credited: [2](#0-1) 
- `push_subscription` unconditionally evicts index 0 once the bounded list is full, regardless of who owns that entry or how much unused value it carries: [3](#0-2) 
- The docs confirm this key is deliberately decoupled from the payer/source chain ("Sponsorship") and that the cap is only expected to be hit under "pathological repeat-buy behavior" — i.e., the eviction design was reasoned about as an accidental self-inflicted edge case, not as an attacker-vs-victim griefing primitive: [4](#0-3) 

Nothing in the gate or the purchase path checks who previously funded a slot before evicting it, and nothing rate-limits or attributes purchases to the target app being credited.

### Impact Explanation
A victim app that legitimately pre-paid for a large tier (e.g., the $1000/8MB tier) can have its entire remaining, unconsumed byte allowance permanently destroyed by a third party who never interacted with the victim's app or its `BandwidthManager` on the victim's chain. This is a direct loss of a paid-for on-chain asset (bandwidth allowance) belonging to the victim, silently reassigned to nobody (the bytes are gone, not stolen but destroyed, breaking the "bandwidth balances must move exactly once and only to the rightful beneficiary and amount" invariant, since the victim's balance is force-drained to zero out-of-band). Once evicted, the victim's app is bricked at the ISMP gate (`BandwidthGate::try_consume` returns `NoAllowance`/`Insufficient`), which can be repeated indefinitely as long as the victim keeps re-purchasing, letting an attacker persistently deny service to a specific app/chain pair while destroying its prepaid funds.

### Likelihood Explanation
The attack requires no privileged role, relayer, prover, or governance action — only calling the public `purchase()` entrypoint on any `BandwidthManager` registered for any source chain, repeated up to 1024 times, each satisfying only `UnknownTier`/`InvalidPurchase` checks (cheapest configured tier). The `chain`/`app` targeting fields are fully attacker-controlled per the intended sponsorship design, so there is no additional bypass needed — it is a direct consequence of the documented data flow. The only friction is the aggregate cost of 1024 cheapest-tier purchases, which bounds severity but does not prevent the attack, and is far cheaper than the value of larger prepaid tiers it can destroy (e.g., 1024×$50 vs. a target's own $1000+ multi-month prepayment, or simply the desire to deny an app service).

### Recommendation
Decouple "who may credit a bucket" from raw eviction risk: e.g., require the eviction target's remaining bytes/value to be below some minimum before silent eviction, refund/credit dust for evicted value instead of destroying it, key `MAX_SUBSCRIPTIONS` per-payer rather than shared across all payers, or require purchases crediting a `(chain, app)` bucket that the caller does not "own" (i.e., `chain != request.source`) to go through a smaller/segregated queue so cross-chain sponsorship can't be weaponized to evict another payer's entries.

### Proof of Concept
1. Victim buys the `TierFour` ($1000/8MB) subscription for `(app_chain = X, app = victim_app)` via `BandwidthManager.purchase()` on any registered source chain.
2. Attacker calls `BandwidthManager.purchase()` (on any registered source chain — does not need to be the same one, or even control `victim_app`) 1024 times with `tier = TierOne`, `months = 1`, `app = victim_app`, `chain = X` — the exact same `(app_chain, app)` key, per `BandwidthPurchaseMsg` fields: [5](#0-4) 
3. Each purchase message is relayed and processed by `on_accept`, calling `push_subscription(&msg.chain, &key, ...)`, growing `Allowance::<T>[X][victim_app]` toward the 1024 cap: [6](#0-5) 
4. On the purchase that pushes the list past capacity, the victim's `TierFour` subscription (now the oldest entry) is evicted and `SubscriptionEvicted { lost_bytes: 8MB }` fires, permanently destroying the victim's remaining unused bandwidth even though the victim never consumed it and no fault of theirs caused it.
5. `BandwidthGate::try_consume(X, victim_app, bytes)` now fails with `NoAllowance`/`Insufficient` for the victim's app, even though it just paid for a full tier.

### Citations

**File:** evm/src/apps/BandwidthManager.sol (L31-40)
```text
struct BandwidthPurchaseMsg {
    /// Recipient app whose bandwidth is being topped up.
    bytes app;
    /// Tier discriminant (matches `pallet_bandwidth::TierIndex`).
    uint256 tier;
    /// Number of tier-windows to credit. Bytes and duration both scale.
    uint256 months;
    /// UTF-8 chain id like `"EVM-8453"` or `"EVM-137"`.
    bytes chain;
}
```

**File:** evm/src/apps/BandwidthManager.sol (L148-170)
```text
    function purchase(bytes calldata app, uint256 tier, uint256 months, bytes calldata chain)
        external
        returns (bytes32 commitment)
    {
        if (app.length == 0 || chain.length == 0 || months == 0) revert InvalidPurchase();
        uint256 price18d = tierPrice[tier];
        if (price18d == 0) revert UnknownTier();

        uint256 total18d = price18d * months;
        address feeToken = IDispatcher(_host).feeToken();
        uint8 dec = IERC20Metadata(feeToken).decimals();
        uint256 scale = 10 ** (18 - dec);
        if (total18d % scale != 0) revert PriceNotRepresentable();
        uint256 amount = total18d / scale;

        IERC20(feeToken).safeTransferFrom(msg.sender, address(this), amount);

        BandwidthPurchaseMsg memory body = BandwidthPurchaseMsg({
            app: app,
            tier: tier,
            months: months,
            chain: chain
        });
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
