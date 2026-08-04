### Title
Unprivileged bandwidth-purchase spam permanently evicts and destroys other users' prepaid, unexpired bandwidth - ([File: modules/pallets/bandwidth/src/lib.rs])

### Summary

### Finding Description
`pallet-bandwidth`'s credit path lets **anyone** on a registered source chain buy bandwidth for **any** `(app_chain, app)` pair — `app` and `chain` are free-form fields inside `BandwidthPurchaseMsg` supplied by the caller of `BandwidthManager.purchase()`, with no ownership check tying the purchase to the app being credited: [1](#0-0) . On the pallet side, `on_accept` only validates that the *manager* is the registered one for the source chain — not that the caller has any relationship to the `app` key being credited: [2](#0-1) .

Every credit — whether legitimate or attacker-issued — is appended to a shared, hard-capped FIFO list via `push_subscription`. When the list for a given `(app_chain, app)` is already at `MAX_SUBSCRIPTIONS` (1024), the **oldest** (head) entry is unconditionally evicted and its `remaining_bytes` are permanently discarded, regardless of whether that entry has already expired or is still fully live/paid-for: [3](#0-2) .

This is the same broken invariant as the external report: a still-valid, paid-for value (Alcx rewards / here, prepaid bandwidth bytes) is destroyed by an action (burning the token / evicting the subscription) without first paying it out or otherwise protecting it. Just as merging a veALCX token burns it without claiming its rewards, pushing a purchase onto a full subscription list burns the head subscription without regard for whether it is still within its paid duration and unconsumed.

Because `app`/`chain` are attacker-controlled and the minimum purchase (`months = 1` against the cheapest configured tier) is a legitimate, unprivileged, self-serve call, an attacker can:
1. Choose a victim `(app_chain, app)` pair known to have real purchased subscriptions.
2. Repeatedly call `purchase()` with `months = 1` and cheap tier pricing, targeting that exact `(chain, app)`.
3. Once the list is full (or already near-full from real traffic), each additional attacker purchase evicts the oldest entry from the head — which, per FIFO/insertion order, will eventually be the victim's real, unexpired, byte-remaining subscription — permanently destroying it and emitting `SubscriptionEvicted` after the fact (an audit trail, not a remedy).

No governance, relayer, prover, or admin compromise is required — the entire path is `purchase()` → ISMP delivery → `on_accept` → `push_subscription`, all public/permissionless entry points guarded only by fee-token payment, not by any relationship to the targeted app.

### Impact Explanation
This is a direct "loss of funds" / "unauthorized... transaction manipulation" class issue matching the bounty scope: an unprivileged third party can force the destruction of another app's already-paid-for, unexpired bandwidth allocation, causing that app's ISMP messages to be rejected by `BandwidthGate::try_consume` (`GateError::NoAllowance`/`Insufficient`) even though it legitimately paid Hyperbridge for guaranteed throughput. The victim has no recourse — subscriptions are immutable, non-refundable, and eviction is irreversible by design (`SubscriptionEvicted` is only an audit event).

### Likelihood Explanation
The attack requires no privileged role, no malicious relayer/prover, and no front-running — only paying the (typically modest) tier price repeatedly for `months = 1` against the attacker-chosen `app`/`chain`. The cost scales with how many entries must be pushed to reach and evict the targeted head entry, which is bounded by `MAX_SUBSCRIPTIONS = 1024`; in a `(chain, app)` bucket that already carries meaningful legitimate traffic (i.e., already near the 1024 cap, which is the exact scenario the eviction mechanism anticipates), the number of attacker purchases needed to reach and evict the next live entry can be small. Given the pallet places no restriction linking `app`/`chain` in the purchase payload to the purchaser, and no minimum "cooldown"/rate limit on repeat purchases into the same bucket, this is a directly reachable, unprivileged griefing/fund-destruction primitive.

### Recommendation
- Tie a purchase's beneficiary `(chain, app)` to some form of authorization (e.g., only the app itself, or an allowlisted payer, may credit its own bucket), or
- Change eviction policy so unexpired subscriptions are never silently destroyed — e.g., reject/queue new purchases instead of evicting live entries, refund the evicted portion pro-rata to whoever originally paid, or raise the cap / use a per-payer sub-limit so one purchaser cannot fill and evict another payer's entries.

### Proof of Concept
1. Governance configures `TierOne` with a small `(bytes, duration_secs)` and registers a `BandwidthManager` for `StateMachine::Evm(X)` per [4](#0-3) .
2. Victim (owner of app `V`) legitimately calls `BandwidthManager.purchase(V, TierOne, 12, "EVM-X")`, crediting a long-duration, large-`remaining_bytes` subscription into `Allowance[EVM-X][V]`.
3. Attacker, with no relationship to `V`, repeatedly calls `BandwidthManager.purchase(V, TierOne, 1, "EVM-X")` (cheapest tier, minimal duration) targeting the same `app = V`.
4. Each attacker purchase is credited via the identical `on_accept` → `push_subscription` path shown in [5](#0-4) ; once `Allowance[EVM-X][V].len() == 1024`, every subsequent attacker purchase evicts the current head.
5. Once the victim's legitimate, still-live subscription reaches the head (FIFO order), the next attacker purchase evicts it and emits `SubscriptionEvicted { app_chain: EVM-X, app: V, tier: TierOne, lost_bytes: <victim's unused bytes> }` — the victim's prepaid, unexpired bandwidth is permanently destroyed even though `V` never authorized or consented to any of the attacker's purchases.

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

**File:** modules/pallets/bandwidth/src/lib.rs (L324-357)
```rust
		/// Push a `Withdraw` message to a remote `BandwidthManager` so
		/// it ships `amount` of `token` to `beneficiary`. Token is
		/// named explicitly because the contract supports recovering
		/// stale fee tokens after a host-side swap.
		#[pallet::call_index(5)]
		#[pallet::weight(T::DbWeight::get().writes(1))]
		pub fn dispatch_withdraw(
			origin: OriginFor<T>,
			target: StateMachine,
			token: H160,
			beneficiary: H160,
			amount: U256,
		) -> DispatchResult {
			<T as pallet_ismp::Config>::AdminOrigin::ensure_origin(origin)?;
			let manager = BandwidthManager::<T>::get(&target).ok_or(Error::<T>::UnknownManager)?;

			let payload = Withdrawal {
				token: alloy_primitives::Address::from(token.0),
				beneficiary: alloy_primitives::Address::from(beneficiary.0),
				amount: to_alloy_u256(amount),
			};
			let mut body = vec![ACTION_WITHDRAW];
			body.extend(payload.abi_encode_params());

			let commitment = Self::dispatch_governance(target, manager, body)?;
			Self::deposit_event(Event::WithdrawalDispatched {
				target,
				token,
				beneficiary,
				amount,
				commitment,
			});
			Ok(())
		}
```

**File:** modules/pallets/bandwidth/src/lib.rs (L404-437)
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

**File:** modules/pallets/bandwidth/src/lib.rs (L455-477)
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
```
