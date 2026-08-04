### Title
Unbounded, permissionless `purchase()` sponsorship lets an attacker evict a victim's paid, unexpired bandwidth subscriptions from the capped FIFO before they can be consumed - ([File: modules/pallets/bandwidth/src/lib.rs])

### Summary
`pallet-bandwidth`'s `Allowance` ledger is a per-`(app_chain, app)` FIFO capped at `MAX_SUBSCRIPTIONS` (1024). Any account on any registered source chain can call `BandwidthManager.purchase()` and set an **arbitrary `app` and arbitrary credit `chain`** in the dispatched `BandwidthPurchaseMsg` — the pallet keys the ledger by the message body's `(chain, app)`, not by `request.source`/sender identity, by design ("sponsorship"). `push_subscription()` evicts strictly by **insertion order** (`list.remove(0)`) whenever the list is full, with no regard to whether the evicted entry is expired, near-expired, or a large, freshly-paid, long-lived subscription.

### Finding Description
The eviction path: [1](#0-0) 

has no protection based on entry value or remaining lifetime — it always evicts index 0 (oldest by insertion), even though the gate drains in the same FIFO order: [2](#0-1) 

Because the app/chain key is taken from the attacker-supplied purchase message body rather than the caller's own identity, anyone can target any victim's `(app_chain, app)` bucket: [3](#0-2) [4](#0-3) 

This is the same broken invariant as the Ajna HPB-dust bug: a bounded FIFO/iteration structure that a permissionless, unprivileged actor can flood with many small entries to override the intent of legitimate participants who put real value into the same structure — here, the mechanism is eviction of the oldest entry instead of iteration exhaustion, but the root cause is identical: **no floor/weight/ownership check gating insertion into a shared, capped ledger that legitimate users depend on for correctness of a later operation** (Ajna: settlement iteration; Hyperbridge: gate consumption FIFO).

Concretely: suppose a victim app has already purchased a large `TierFour` (8MB) subscription for `(APP_CHAIN, victimApp)`, which sits at FIFO position 0 with a fixed multi-month expiry. An attacker who knows (or targets) the victim's `(chain, app)` pair can repeatedly call `purchase()` with `TierOne` and `months=1` (cheapest configured tier) 1024 times, since nothing restricts who may credit an `(app_chain, app)` bucket — this is the intended cross-chain sponsorship feature. Each of these pushes appends behind the victim's entry, and once the list is at cap, `push_subscription` evicts index 0 — the victim's still-valid, unconsumed, real-money subscription — silently, only emitting `SubscriptionEvicted` after the fact: [5](#0-4) 

The test suite confirms this exact mechanic works as coded, with no floor on eviction value: [6](#0-5) 

### Impact Explanation
This causes direct, provable loss of prepaid, unexpired funds for the victim app: bandwidth the victim already paid for (in the fee token, on the EVM side) is permanently lost from the ledger before the app can drain it via `BandwidthGate::try_consume`. This is a "loss of funds" / logic-attack class bug matching the bounty's accepted impact ("stealing or loss of funds", "logic attacks") — reachable through a fully public, unprivileged entrypoint (`purchase()`), requiring no malicious relayer, prover, or admin. The attacker doesn't need to compromise anything; they only need to pay for cheap tier purchases targeting the victim's known `(chain, app)` key, which is discoverable from the victim's own `BandwidthCredited`/`BandwidthPurchased` events.

### Likelihood Explanation
Medium-to-High. The attack requires the attacker to spend real funds (`tier.price × months` per dust purchase, up to 1024 times to guarantee eviction of a specific victim entry, fewer if targeting recently active low-cap apps), so it is not free, but it is entirely mechanical, deterministic, and requires no special access — anyone can call `BandwidthManager.purchase()` on any registered source chain and set any `app`/`chain` pair. Apps with modest existing subscription counts (well below 1024) are especially vulnerable to targeted, cheaper eviction campaigns. The `(chain, app)` targeting information is public via on-chain events, so identifying a victim is trivial.

### Recommendation
- Do not evict purely by insertion order; only evict entries that are already expired, or require the evicted entry's `remaining_bytes` to be below some floor before an unrelated payer's push can displace it.
- Alternatively, cap sponsorship: require `force_credit`-style privileged sponsorship for third-party `(chain, app)` pairs, or require `request.from` payer identity match `app` for self-funded purchases, with a separate, more restrictive path (e.g., minimum bytes-per-eviction, or per-payer sub-limits) for cross-chain sponsorship.
- When the list is full, prefer evicting the *lowest remaining-value* entry (or the soonest-to-expire) rather than strictly FIFO-oldest, so an attacker cannot deterministically target a specific valuable entry with pure insertion-order griefing.
- Emit `SubscriptionEvicted` pre-emptively/reject the push if the evicted entry still has more than some minimum remaining value, forcing an explicit governance/consent path for high-value evictions.

### Proof of Concept
1. Victim purchases `TierFour` (8 MB, e.g. 6-month duration) for `(EVM-8453, victimApp)` via `BandwidthManager.purchase()`; this lands as the sole/oldest entry in `Allowance::<T>::get(EVM-8453, victimApp)`.
2. Attacker observes the `BandwidthCredited` event revealing `(app_chain=EVM-8453, app=victimApp)`.
3. Attacker calls `purchase(app=victimApp, tier=TierOne, months=1, chain="EVM-8453")` up to `MAX_SUBSCRIPTIONS` (1024) times from their own account (or fewer times if the victim's list isn't already near cap) — nothing in `on_accept`/`push_subscription` restricts who may credit `victimApp`'s bucket: [7](#0-6) 
4. As soon as the list reaches `MAX_SUBSCRIPTIONS`, subsequent attacker pushes evict index 0 — the victim's `TierFour` subscription, which had months of remaining validity and had not yet been drained by the gate — exactly as demonstrated mechanically in `subscription_cap_evicts_oldest` (using `force_credit`, which shares `push_subscription` with `purchase`): [8](#0-7) 
5. The victim's already-paid, real bandwidth allowance is now gone from storage; `Bandwidth::remaining(&EVM-8453, &victimApp)` reflects only the attacker's dust subscriptions, and any real request traffic from the victim gets rejected via `GateError::NoAllowance`/`Insufficient` once those dust bytes are exhausted, despite the victim having paid for far more.

### Citations

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

**File:** modules/pallets/bandwidth/src/lib.rs (L404-425)
```rust
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
```

**File:** modules/pallets/bandwidth/src/lib.rs (L455-489)
```rust
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

**File:** evm/src/apps/BandwidthManager.sol (L148-180)
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

        commitment = IDispatcher(_host).dispatch(
            DispatchPost({
                dest: IDispatcher(_host).hyperbridge(),
                to: PALLET_BANDWIDTH_MODULE_ID,
                body: abi.encode(body),
                timeout: 0,
                fee: 0,
                payer: address(this)
            })
```

**File:** modules/pallets/testsuite/src/tests/pallet_bandwidth.rs (L523-574)
```rust
/// The 1024-sub cap evicts the oldest entry. force_credit reuses the
/// same push path as purchase, so this also covers the purchase cap.
#[test]
fn subscription_cap_evicts_oldest() {
	new_test_ext().execute_with(|| {
		jump_to(T0);
		let cap = MAX_SUBSCRIPTIONS as u128;

		// Fill the list to exactly the cap. `bytes` encodes the index
		// so we can prove which one got evicted.
		for i in 0..cap {
			Bandwidth::force_credit(
				RuntimeOrigin::root(),
				ForceCreditParams {
					app_chain: APP_CHAIN,
					app: app_key(),
					tier: TIER1,
					bytes: i + 1,
					duration_secs: MONTH_SECS,
				},
			)
			.unwrap();
		}
		assert_eq!(sub_count(APP_CHAIN), cap as usize);
		assert_eq!(sub_at(APP_CHAIN, 0).unwrap().remaining_bytes, 1, "oldest is index 1");

		// One more push: evicts the oldest, appends the new one.
		Bandwidth::force_credit(
			RuntimeOrigin::root(),
			ForceCreditParams {
				app_chain: APP_CHAIN,
				app: app_key(),
				tier: TIER1,
				bytes: cap + 1,
				duration_secs: MONTH_SECS,
			},
		)
		.unwrap();

		assert_eq!(sub_count(APP_CHAIN), cap as usize, "still capped");
		assert_eq!(
			sub_at(APP_CHAIN, 0).unwrap().remaining_bytes,
			2,
			"former second-oldest is now front",
		);
		assert_eq!(
			sub_at(APP_CHAIN, (cap - 1) as usize).unwrap().remaining_bytes,
			cap + 1,
			"new sub is at the back",
		);
	});
}
```
