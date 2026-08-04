## Analysis

The seed report's core pattern: a public entrypoint lets an unprivileged attacker **force an unrequested state change onto a victim's identity/slot**, and because the victim's protective invariant ("you already have a proxy" / here: "your subscription list is safe") is bypassed, the attacker can degrade or destroy value the victim already holds, at any time, with no relayer/prover/admin involvement.

The local analog is `pallet-bandwidth`'s bandwidth-purchase → FIFO-subscription-eviction path.

### Title
Unauthorized forced eviction of a victim app's paid bandwidth via `BandwidthManager.purchase()` — ([File: evm/src/apps/BandwidthManager.sol], [File: modules/pallets/bandwidth/src/lib.rs])

### Summary
`BandwidthManager.purchase()` is a fully public, unpermissioned entrypoint that lets **any caller** credit a bandwidth subscription to **any arbitrary `app`/`chain` pair** of their choosing, not just their own app [1](#0-0) . On the Hyperbridge side, `pallet_bandwidth::on_accept` only checks that the message came from the registered `BandwidthManager` contract — it performs no check that the caller who invoked `purchase()` has any relationship with the `app`/`chain` being credited [2](#0-1) . Each credit is appended to a FIFO list capped at `MAX_SUBSCRIPTIONS = 1024`; once full, **the oldest entry is unconditionally evicted** regardless of who paid for it or how many bytes remain unused [3](#0-2) [4](#0-3) .

### Finding Description
Just as `PRBProxyRegistry.deployFor()`/`transferOwnership()` let an attacker force an unwanted ownership assignment onto a victim's proxy slot at any time (docs describe the analogous flaw), `BandwidthManager.purchase(app, tier, months, chain)` lets an attacker force unwanted state changes onto a **victim app's** subscription queue at any time — the `app` and `chain` parameters are attacker-supplied, arbitrary bytes with no authentication tying them to `msg.sender` [5](#0-4) .

This is intentionally supported as a "sponsorship" feature (crediting someone else's app is documented as a feature) [6](#0-5) , but the FIFO cap turns this benign-looking design into an attack primitive: an attacker who wants to grief a victim app only needs to repeatedly call `purchase()` targeting the victim's `(app, chain)` key with the cheapest configured tier. Since `push_subscription` evicts unconditionally at the 1024 cap with no ownership or "protect legitimate holder" check [7](#0-6) , filling the queue with 1024 cheap purchases pushes out the victim's genuinely-paid, unexpired, high-value subscriptions — which the pallet acknowledges are "lost" (`SubscriptionEvicted { lost_bytes }`) [8](#0-7) .

The existing "no mutation on insufficient" guard in the gate (`try_consume`) only protects against half-draining during a race [9](#0-8)  — it does nothing to stop this eviction-via-spam-credit vector, because eviction happens on the **credit** path (`push_subscription`), not the consume path.

### Impact Explanation
A victim app that has paid for a large, long-duration bandwidth tier (e.g. the $1000/8MB/30-day plan) can have that unused, already-paid allowance evicted and permanently lost before it's consumed, simply by an attacker spamming 1024 cheap purchases against the same `(app, chain)` key. This is a direct loss of funds already paid to the protocol — the victim paid for bytes that are wiped from the ledger with no refund mechanism, and the attacker doesn't need to be a relayer, prover, or governance actor; `purchase()` is a completely public, unprivileged EVM call.

### Likelihood Explanation
High: the attacker only needs enough fee-token balance to buy the cheapest configured tier 1024 times (bounded, attacker-controlled cost) and knowledge of the victim's `app`/`chain` identifiers, both of which are public (emitted in `BandwidthCredited`/`BandwidthPurchased` events) [10](#0-9) [11](#0-10) . No relayer collusion, no admin compromise, and no race condition timing is required — a straightforward sequence of public transactions suffices.

### Recommendation
Either (a) restrict `push_subscription` eviction so it cannot evict a subscription with significant unused `remaining_bytes` purchased by a different payer/tier than the one currently pushing, (b) require attribution/consent for third-party credits (e.g. an allow-list of sponsors per app, mirroring the two-step consent pattern already used elsewhere in this codebase for `pallet-collator-manager`'s controller pairing) [12](#0-11) , or (c) raise/segment the FIFO cap per payer so one payer's spam purchases cannot evict another payer's entries for the same `(app, chain)`.

### Proof of Concept
1. Victim app `V` on chain `C` purchases Tier 4 (8MB / 30-day) once — one `Subscription` entry with large `remaining_bytes` and a 30-day `expires_at` is pushed to `Allowance[C][V]`.
2. Attacker calls `BandwidthManager.purchase(V, TIER1, 1, C)` 1024 times (any `msg.sender`, any tier the attacker can afford, arbitrary `app`/`chain` values matching the victim's).
3. Each call dispatches a `BandwidthPurchaseMsg` to `pallet-bandwidth`; `on_accept` decodes it and calls `push_subscription(&C, &V, TIER1, bytes, duration)` [13](#0-12) .
4. Once `Allowance[C][V]` reaches 1024 entries, each subsequent attacker purchase evicts the oldest entry — eventually the victim's Tier-4 subscription reaches the front and gets evicted via `list.remove(0)` [7](#0-6) , emitting `SubscriptionEvicted` with the victim's `lost_bytes`.
5. The victim's paid-for, unexpired bandwidth allowance is permanently gone with no refund path.

### Citations

**File:** evm/src/apps/BandwidthManager.sol (L90-99)
```text
    event BandwidthPurchased(
        address indexed payer,
        address feeToken,
        uint256 tier,
        uint256 months,
        uint256 amountPaid,
        bytes app,
        bytes chain,
        bytes32 commitment
    );
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

**File:** modules/pallets/bandwidth/src/lib.rs (L141-151)
```rust
		/// A new subscription was appended on the `(app_chain, app)`
		/// list as a result of a paid purchase from `paid_from`.
		BandwidthCredited {
			app_chain: StateMachine,
			app: AppKey,
			/// Chain that paid; differs from `app_chain` on sponsorship.
			paid_from: StateMachine,
			tier: TierIndex,
			bytes: BandwidthBytes,
			expires_at: u64,
		},
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

**File:** modules/pallets/bandwidth/src/types.rs (L19-22)
```rust
/// Hard cap on the subscription list per `(chain, app)`. Pushes
/// beyond this evict the oldest entry (FIFO).
pub const MAX_SUBSCRIPTIONS: u32 = 1024;
pub type MaxSubscriptions = ConstU32<MAX_SUBSCRIPTIONS>;
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L108-114)
```text
## Sponsorship

The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
```

**File:** modules/pallets/collator-manager/src/lib.rs (L284-310)
```rust
		/// Registers a controller account for a bonded stash.
		///
		/// The origin must be a stash account, which must have already bonded funds
		/// via `pallet-collator-selection`. The supplied `controller` must have
		/// previously authorised the pairing by calling `approve_controller` from
		/// the controller's own origin — without this two-step consent, an
		/// arbitrary stash could squat any unpaired controller address, blocking
		/// the legitimate operator and (if the controller carried session keys
		/// and reputation) consuming that reputation on selection.
		#[pallet::call_index(1)]
		#[pallet::weight(<T as pallet::Config>::WeightInfo::register())]
		pub fn register(origin: OriginFor<T>, controller: T::AccountId) -> DispatchResult {
			let stash = ensure_signed(origin)?;
			ensure!(!Controller::<T>::contains_key(&stash), Error::<T>::AlreadyRegistered);
			ensure!(!Stash::<T>::contains_key(&controller), Error::<T>::AlreadyPaired);
			// Controller must have signed an approval for this specific stash.
			ensure!(
				ControllerApprovals::<T>::take(&stash, &controller).is_some(),
				Error::<T>::ControllerApprovalMissing
			);

			Controller::<T>::insert(&stash, &controller);
			Stash::<T>::insert(&controller, &stash);

			Self::deposit_event(Event::Registered { stash, controller });
			Ok(())
		}
```
