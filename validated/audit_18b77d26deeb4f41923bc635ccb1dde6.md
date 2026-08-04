## Analysis

The Fenwick-tree bug's core invariant is: **an unprivileged caller can register entries in a bounded, per-user data structure on behalf of an arbitrary victim, and once the structure is full, all further legitimate registrations are blocked/lost.** The direct local analog in Hyperbridge is the bandwidth-subscription FIFO in `pallet-bandwidth`, credited via `BandwidthManager.purchase()`.

`purchase()` on the EVM side takes an arbitrary `app` byte-string as the credit recipient with no restriction tying it to `msg.sender` — anyone can pay to credit *any* app on *any* chain: [1](#0-0) 

On the pallet side, each `(app_chain, app)` pair holds a `BoundedVec<Subscription, MaxSubscriptions>` capped at 1024, keyed purely by the `app` field taken from the message body (not from any authenticated identity): [2](#0-1) 

Pushing past the cap **silently evicts the oldest entry** rather than reverting: [3](#0-2) 

### Title
Permissionless `BandwidthManager.purchase()` lets an attacker grief/evict a victim app's paid bandwidth subscriptions via FIFO-cap eviction - (File: `evm/src/apps/BandwidthManager.sol`, `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`purchase()` accepts an arbitrary `app` recipient chosen entirely by the caller, with the fee paid by the caller but the resulting subscription credited to any target `(chain, app)` bucket. Since that bucket is a `BoundedVec` capped at `MAX_SUBSCRIPTIONS = 1024` that evicts the *oldest* entry once full [4](#0-3) , an attacker can repeatedly purchase the cheapest tier for a victim's `app` identifier, filling the FIFO with 1024 cheap, attacker-funded, near-worthless subscriptions. Any subsequent purchase — either by the attacker or, more importantly, once the victim's *real* large-tier subscriptions eventually age to the front of the queue — evicts real paid entries, permanently destroying the bytes the victim already paid for (`SubscriptionEvicted` only logs the loss, it never refunds it) [5](#0-4) .

### Finding Description
- `purchase()` has no check that `app == msg.sender` or any ownership binding between the payer and the credited app: [6](#0-5) 
- The pallet accepts the purchase purely based on `request.from` matching the registered manager for the source chain, not on any relationship between payer and the `app` field inside the message body: [7](#0-6) 
- `push_subscription` enforces only the 1024-entry cap per `(app_chain, app)` key and evicts index 0 (oldest) once full, with no distinction between "attacker-funded junk entries" and "victim-funded real entries": [8](#0-7) 
- The eviction path is exercised and confirmed correct-but-unconditional by the test suite (any push once at cap evicts oldest, regardless of provenance): [9](#0-8) 

Because same-tier repurchases "queue" rather than "stack" (documented behavior), an attacker doesn't need to compromise anything — they just need to call `purchase()` 1024+ times against the smallest configured tier for the victim's `app`/`chain` pair. Since `purchase()` is a plain external function pullable by anyone with the fee token, and the cheapest tier ($50/100KB per the docs) is far smaller than what a serious integrator would buy in bulk, an attacker can cheaply exhaust the FIFO capacity for a target app, and any subsequent purchase (attacker's own next buy, or the victim topping up) evicts the oldest entry — which, after 1024 attacker fills, is guaranteed to be attacker junk until it's all evicted, at which point the *victim's own paid, unexpired, high-value subscriptions* start getting evicted by further attacker spam, permanently destroying paid-for bandwidth allowance the victim already paid Hyperbridge fee-tokens for.

### Impact Explanation
This is loss of funds for the victim app: a subscription's `remaining_bytes` represents fee-tokens already collected and spent by `BandwidthManager` (pulled via `safeTransferFrom` at purchase time) [10](#0-9) ; once evicted, that paid allowance is gone with no refund path, and the app's ISMP messages start getting rejected by the gate (`GateError::NoAllowance`/`Insufficient`) [11](#0-10) , causing denial of legitimate cross-chain dispatch for an app that has already paid to avoid exactly that outcome.

### Likelihood Explanation
Medium: the attacker must spend real fee-token on ≥1024 cheap-tier purchases (real cost, not free spam), same as the original report noting the Fenwick-tree griefing "can only be reset after lock duration" — here there's no user-side reset at all short of buying enough of their own subscriptions to push the attacker's junk back out. The attack is unprivileged, requires no relayer/prover/admin compromise, and is directly reachable through the public `purchase()` entrypoint on any registered source chain.

### Recommendation
Bind `app` to `msg.sender` in `BandwidthManager.purchase()` (or require an explicit authorization/allowlist from the app being credited) so a third party cannot force-fill another app's FIFO. Alternatively, on the pallet side, segregate or rate-limit credits by payer identity per `(app_chain, app)`, or refuse to evict subscriptions whose remaining value exceeds some threshold / haven't been "claimed" by their rightful owner, mirroring the external report's recommendation to check the caller before mutating another party's bounded ledger.

### Proof of Concept
1. Governance configures `TierIndex::TierOne` with a small byte/duration budget and registers a `BandwidthManager` for `EVM-<sourceChain>` [12](#0-11) .
2. Victim app `V` buys a large tier (e.g. TierFour) once, crediting `Allowance[app_chain][V]` with a large `remaining_bytes` subscription.
3. Attacker calls `BandwidthManager.purchase(app = V, tier = TierOne, months = 1, chain = app_chain)` 1024+ times, each dispatching a `BandwidthPurchaseMsg` crediting `V`'s bucket [13](#0-12) .
4. Once `Allowance[app_chain][V].len() == 1024`, `push_subscription` evicts index 0 on every further purchase [14](#0-13) ; after enough attacker purchases, `V`'s original TierFour subscription reaches the front and is evicted, destroying the bytes `V` paid for.
5. `V`'s subsequent ISMP dispatches are rejected by `BandwidthGate::try_consume` with `NoAllowance`/`Insufficient` despite `V` having paid for bandwidth that was never consumed but was evicted out from under them [11](#0-10) .

### Citations

**File:** evm/src/apps/BandwidthManager.sol (L143-193)
```text
    /// @param app Recipient app address (usually 20-byte EVM, packed as bytes).
    /// @param tier Tier discriminant; must be configured via `SetTiers`.
    /// @param months Number of tier-windows to credit; must be > 0.
    /// @param chain UTF-8 chain id (e.g. `"EVM-8453"`) of the credit chain.
    /// @return commitment Hyperbridge dispatch commitment for tracking.
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

        emit BandwidthPurchased({
            payer: msg.sender,
            feeToken: feeToken,
            tier: tier,
            months: months,
            amountPaid: amount,
            app: app,
            chain: chain,
            commitment: commitment
        });
    }
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

**File:** modules/pallets/bandwidth/src/lib.rs (L214-225)
```rust
		#[pallet::call_index(0)]
		#[pallet::weight(T::DbWeight::get().writes(1))]
		pub fn set_manager(
			origin: OriginFor<T>,
			source: StateMachine,
			manager: H160,
		) -> DispatchResult {
			<T as pallet_ismp::Config>::AdminOrigin::ensure_origin(origin)?;
			BandwidthManager::<T>::insert(source, manager);
			Self::deposit_event(Event::ManagerRegistered { source, manager });
			Ok(())
		}
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

**File:** modules/pallets/bandwidth/src/lib.rs (L439-445)
```rust
		/// The router uses this to skip the gate on purchases —
		/// otherwise a depleted app couldn't recharge.
		pub fn is_purchase_message(request: &PostRequest) -> bool {
			BandwidthManager::<T>::get(&request.source)
				.map(|m| request.from == m.0.to_vec())
				.unwrap_or(false)
		}
```

**File:** modules/pallets/bandwidth/src/lib.rs (L509-534)
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
```

**File:** modules/pallets/bandwidth/src/types.rs (L19-22)
```rust
/// Hard cap on the subscription list per `(chain, app)`. Pushes
/// beyond this evict the oldest entry (FIFO).
pub const MAX_SUBSCRIPTIONS: u32 = 1024;
pub type MaxSubscriptions = ConstU32<MAX_SUBSCRIPTIONS>;
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
