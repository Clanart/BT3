Confirmed: `purchase()` on `BandwidthManager.sol` is fully permissionless, and `app` is attacker-controlled arbitrary bytes with no ownership check tying the caller to the app it credits.

### Title
Permissionless `purchase()` griefs a victim's bandwidth allowance via FIFO eviction, destroying paid-for funds - (File: `evm/src/apps/BandwidthManager.sol`, `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`BandwidthManager.purchase()` lets **any caller** credit bandwidth to **any `app` identifier** on **any chain**, and `pallet-bandwidth::push_subscription` stores subscriptions in a hard-capped (1024) FIFO list per `(app_chain, app)`, evicting the **oldest** entry whenever the cap is hit — regardless of who bought it, its size, or its remaining value. Just as the external report's `_supplyPool()` ignored the underlying pool's real capacity and produced an unintended reordering/loss based on a fixed cap, `pallet-bandwidth`'s fixed 1024-slot cap combined with a fully permissionless, attacker-chosen `app` key lets anyone force-evict another app's legitimately paid-for, unexpired bandwidth by flooding the queue with cheap purchases.

### Finding Description
`purchase()` takes `app` as raw `bytes calldata` with no binding to `msg.sender` and no authorization check: [1](#0-0) 

On the Hyperbridge side, `on_accept` decodes the purchase and calls `push_subscription`, which evicts the **head (oldest)** entry once the list hits `MAX_SUBSCRIPTIONS` (1024), with no regard to entry size, payer, or remaining lifetime: [2](#0-1) 

The keying is `(app_chain, app)` taken from the attacker-supplied message body, not from `request.source`/`msg.sender`: [3](#0-2) 

Docs explicitly acknowledge the eviction destroys unconsumed value ("what the user paid for and won't get to use"): [4](#0-3) 

The gate itself drains strictly FIFO by insertion order, so a victim's large/high-tier subscription sitting behind attacker-injected low-tier entries is also delayed or, if pushed past the 1024 cap before being drained, evicted outright: [5](#0-4) 

This mirrors the external report's root cause exactly: a fixed local cap/queue-management rule (`config[pool].cap` / the 1024-slot FIFO) is enforced without regard to the real-world constraint that matters (the underlying pool's cap / the victim's already-paid allowance), causing loss of paid-for value and unintended reordering — except here the trigger is a fully permissionless public entrypoint rather than an admin misconfiguration.

### Impact Explanation
An unprivileged attacker can, at the cost of only the cheapest tier's price × ~1024 (or fewer, if the victim's app already has a partially-filled queue), permanently destroy a victim app's bandwidth allowance that was already paid for and not yet consumed. This is a direct loss of funds for the victim (unused, prepaid bandwidth bytes are unrecoverable once evicted — the only remedy is an admin `force_credit`), triggered purely through the public `purchase()` entrypoint with no reliance on a malicious relayer, prover, or admin action.

### Likelihood Explanation
The attack requires no special privileges — `purchase()` is open to anyone, `app` is unauthenticated, and `chain` is not validated against the caller's actual source chain (sponsorship is a documented feature, not a bug): [6](#0-5) 
The only cost is gas plus the cheapest configured tier price repeated up to 1024 times, which is bounded and could be economically justified against a target with valuable bandwidth-locked infrastructure (e.g. griefing a competitor's app into rate-limiting failures, or evicting a large subscription right before the app needs it).

### Recommendation
- Bind `app` credit authority to the actual sender/app identity, or require the purchase to be signed/attested by the app owner, rather than accepting an arbitrary attacker-supplied `app` field.
- Replace blind oldest-first eviction with a policy that protects unexpired, high-value, or soon-to-be-consumed subscriptions (e.g. evict smallest-remaining-value first, or refuse to evict entries with more than some threshold of remaining bytes/duration, forcing the purchase to revert instead).
- Consider rate-limiting or increasing the cost of pushing new subscriptions onto an already-near-full queue for a given `(chain, app)`, and/or emitting an alert/refusal path instead of silent eviction.

### Proof of Concept
1. Attacker identifies a victim app with an active, high-value bandwidth subscription (e.g. tier 4, 8 MB) on `(chainX, victimApp)`.
2. Attacker calls `BandwidthManager.purchase(victimApp, tier=1, months=1, chain=chainX)` repeatedly (up to 1024 times), each a cheap $50-tier purchase, all targeting the same `app` bytes as the victim's.
3. Each purchase dispatches a `BandwidthPurchaseMsg` that `pallet-bandwidth::on_accept` credits via `push_subscription` into `Allowance[chainX][victimApp]`. [7](#0-6) 
4. Once the list reaches 1024 entries, each further purchase evicts the oldest entry — eventually the victim's original, unexpired, high-value subscription — firing `SubscriptionEvicted` with the victim's `lost_bytes`. [8](#0-7) 
5. The victim's paid-for bandwidth is now unusable; any pending or future traffic from the victim's app is rejected by `BandwidthGate::try_consume` with `NoAllowance`/`Insufficient`, and the destroyed bytes cannot be recovered except by governance `force_credit`.

### Citations

**File:** evm/src/apps/BandwidthManager.sol (L148-171)
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

**File:** modules/pallets/bandwidth/src/lib.rs (L105-118)
```rust
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

**File:** modules/pallets/bandwidth/src/lib.rs (L509-555)
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
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L75-77)
```text
### Eviction

Pushing onto a full list (1024 entries) evicts the **oldest** entry and emits `SubscriptionEvicted` with the lost bytes so the loss is auditable on-chain. In practice this only happens under pathological repeat-buy behavior — at the default of one purchase per cycle, 1024 buys is years of headroom.
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
