## Title
Unauthenticated `purchase()` allows any third party to force-fill and evict a victim app's paid bandwidth subscriptions - (File: `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`pallet-bandwidth`'s subscription ledger is keyed purely by `(app_chain, app)` taken from an untrusted purchase message, with no authorization tying a purchase to the app owner's consent. Because the FIFO list is capped at `MAX_SUBSCRIPTIONS = 1024` and full pushes silently evict the oldest entry, any unprivileged caller can spam cheap purchases targeting a victim `(chain, app)` pair to force-evict the victim's genuinely paid-for, larger/longer subscriptions — an unwanted, uninvited mutation of a beneficiary's balance, structurally analogous to the JBTiered721Delegate credit-hijack bug where an attacker uses a beneficiary's balance/slot without consent to cause an inferior outcome.

### Finding Description
`BandwidthManager.purchase()` [1](#0-0)  is a fully public entry point: `msg.sender` pays the fee token, but the `app` and `chain` (credit target) parameters are attacker-controlled and are **not validated against `msg.sender`** — this is the documented "sponsorship" feature [2](#0-1) .

On Hyperbridge, `on_accept` decodes the purchase message and calls `push_subscription` keyed by `msg.chain`/`msg.app` from the message body — again with no check that the purchaser is the app owner or has any relationship to it [3](#0-2) .

`push_subscription` appends unconditionally, and once the list hits `MAX_SUBSCRIPTIONS` (1024), it evicts index 0 (the oldest, not necessarily the least valuable) FIFO entry, regardless of that entry's remaining bytes or how much was paid for it: [4](#0-3) 

There is no per-purchaser or per-app rate limit, no minimum purchase value proportional to eviction risk, and no way for the app owner to opt out of unsolicited third-party credits. The only "guard" — the `MAX_SUBSCRIPTIONS` bound — is exactly what turns into the attack surface: an attacker who issues enough minimal purchases (`tier` × `months=1`, the cheapest configured SKU) against a target `(chain, app)` will progressively evict older entries, including any large/long-duration subscription the legitimate owner or its sponsors paid for.

This mirrors the external report's broken invariant: a party who did not consent to a mutation of the victim's balance/slot can force it, using only a small individual payment, and the FIFO/cap "credit" accounting has no consent gate.

### Impact Explanation
This falls under "unauthorized transaction/execution" and "loss of funds" categories relevant to the bounty: an app that legitimately purchased significant bandwidth (e.g., the $1000/8MB tier) can have that paid-for allowance evicted and permanently lost — `SubscriptionEvicted` only logs the loss, it does not refund or protect it [5](#0-4) . Losing bandwidth causes the ISMP router's gate to reject the app's legitimate cross-chain dispatches once the queue is drained/exhausted [6](#0-5) , effectively a funded denial-of-service against a specific application's cross-chain messaging, achievable by any unprivileged EVM account with no relayer, prover, or governance role involved.

### Likelihood Explanation
Medium. It requires an attacker to fund 1024 minimal purchases against the target `(chain, app)` to force the queue to wrap and start evicting — a real but bounded and fully permissionless cost (using the cheapest configured tier at `months=1`), and does not require any privileged actor, malicious relayer, or race condition. The attack is straightforward, deterministic, and repeatable against any app using the bandwidth model.

### Recommendation
- Restrict who may credit an app's allowance (e.g., require `msg.sender == app` on the EVM side, or an explicit allow-list of approved sponsors per app), or
- When evicting under the cap, evict based on value/remaining-bytes/expiry rather than strict FIFO, and/or reject third-party purchases that would evict a not-yet-consumed subscription with higher remaining value than the incoming one, or
- Increase `MAX_SUBSCRIPTIONS` bound dynamically / charge an anti-spam cost proportional to queue depth so cheap purchases cannot cheaply displace expensive ones, and emit a way for app owners to lock/protect specific subscriptions from eviction.

### Proof of Concept
1. Victim app `A` on chain `X` legitimately purchases `TierFour` (8MB / $1000) bandwidth via `BandwidthManager.purchase(A, TierFour, 1, "EVM-X")` — pallet appends this as one `Subscription` in `Allowance[X][A]`.
2. Attacker, with no relationship to `A`, repeatedly calls `purchase(A, TierOne, 1, "EVM-X")` (cheapest tier) 1024 times from any source chain with a registered `BandwidthManager`.
3. Each call succeeds `on_accept` unconditionally (only `request.from == registered manager` is checked, not who is being credited) [7](#0-6) , appending to `Allowance[X][A]`.
4. Once `Allowance[X][A].len() == 1024`, subsequent pushes evict index 0 FIFO [8](#0-7)  — the victim's `TierFour` subscription (if it was purchased before the attacker's spam sequence) is evicted once enough entries have been pushed past it, permanently destroying the $1000-paid allowance with only `SubscriptionEvicted` emitted as an audit trail; no funds are refunded.

### Citations

**File:** evm/src/apps/BandwidthManager.sol (L148-181)
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
        );
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L108-114)
```text
## Sponsorship

The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
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
