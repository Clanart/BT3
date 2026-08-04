## Title
Permissionless `BandwidthManager.purchase()` lets an attacker cheaply evict a victim app's paid-for bandwidth subscriptions - (File: `modules/pallets/bandwidth/src/lib.rs`, `evm/src/apps/BandwidthManager.sol`)

### Summary
The seed report's core invariant break is: an unprivileged actor can repeatedly append cheap entries into a victim-controlled, capped array via a public entrypoint, and the array's "cannot shrink, only grows/evicts" behavior lets the attacker either lock the victim out (Velodrome) or destroy value the victim already paid for. In `pallet-bandwidth`, the `(app_chain, app)` subscription list is a `BoundedVec` capped at 1024 that evicts the *oldest* entry once full [1](#0-0) , and any address can push a new subscription onto *any* target `app`'s list simply by calling the public, unauthenticated `purchase()` function on `BandwidthManager.sol` [2](#0-1) .

### Finding Description
`purchase()` takes an arbitrary `app` byte-string and `chain` byte-string chosen entirely by the caller — there is no check that the caller owns or controls the target app [3](#0-2) . This is intentional for the "sponsorship" feature (a payer can top up bandwidth for any app on any chain) [4](#0-3) .

On the Hyperbridge side, `on_accept` in `pallet-bandwidth` only checks that the *source chain + sender* matches the registered `BandwidthManager` for that chain — it performs no check on which `app` is targeted, and then calls `push_subscription` for whatever `(chain, app)` key is embedded in the purchase message [5](#0-4) .

`push_subscription` enforces the 1024-entry cap by unconditionally evicting index `0` (the oldest entry, by FIFO/insertion order) whenever the list is full, regardless of that entry's remaining value:
```rust
let evicted = Allowance::<T>::mutate(app_chain, app, |list| {
    let evicted = if list.len() == MAX_SUBSCRIPTIONS as usize {
        Some(list.remove(0))
    } else {
        None
    };
    let _ = list.try_push(new_sub);
    evicted
});
``` [6](#0-5) 

Because tier price is a fixed per-purchase cost independent of the *value* of the entry being evicted, an attacker can pay for the cheapest configured tier (e.g. TierOne, `$50/100KB`, `months = 1`) repeatedly against a specific `(chain, app)` pair to fill the list to the 1024 cap, and then continue purchasing the same cheap tier to evict — one at a time — whatever legitimate, larger subscriptions the victim app previously paid for (e.g. TierFour, `$1000/8MB`) [7](#0-6) . Each eviction only emits `SubscriptionEvicted` for auditability — there is no refund or compensation to the victim [8](#0-7) [9](#0-8) .

This is structurally the same broken invariant as the Velodrome `_burn`/`dstRep` bug: a public, unprivileged entrypoint lets an attacker append into a shared bounded ledger keyed by a victim identifier, and the ledger's fixed-cap/no-shrink semantics convert that append into loss for the victim — here, permanent destruction of already-paid-for bandwidth instead of a deposit DoS.

### Impact Explanation
Bandwidth is a prepaid, real-money asset (fee-token debited at `purchase()` time) [10](#0-9) . An attacker who never interacted with the victim can, at a cost of one cheap-tier purchase per eviction, destroy an arbitrarily larger legitimate subscription belonging to that victim app, since the eviction target is always the FIFO head regardless of tier/value. This is "loss of funds" for the victim under the bounty's impact gate: an unprivileged party can force the loss of an asset (paid bandwidth allowance) that rightfully belongs to another app, at an economically favorable ratio to the attacker (spend ~$50 to destroy up to ~$1000 of remaining bandwidth per eviction, repeatable). Because bandwidth gates all non-purchase message dispatch for the app [11](#0-10) , sustained griefing can also stall the victim's cross-chain messaging entirely once its allowance is drained/evicted faster than it can top up.

### Likelihood Explanation
The attack requires only an EOA calling the public `purchase()` function with an attacker-chosen `app`/`chain` — no relayer, prover, admin, or governance role is needed, and no malformed proof or malicious peer is involved. The only barrier is the attacker's own capital to pay tier prices, but because the cost of destruction is decoupled from the value destroyed (fixed per-purchase price vs. arbitrary victim entry size), the attack is economically viable against any app that purchases larger/longer tiers. This qualifies as a locally provable, unprivileged, public-entrypoint issue rather than a griefing-only or malicious-operator scenario.

### Recommendation
- Restrict who can top up an app's bandwidth (e.g., require the purchase to originate `from` the app address itself, or from an explicitly-approved sponsor list per app), removing unrestricted third-party writes into another app's FIFO list.
- Alternatively/also, change eviction policy so that it does not blindly evict the oldest entry irrespective of value — e.g., evict the entry with least remaining value, or reject new purchases once the list is full instead of silently destroying existing paid balance, and/or refund the fee-token-equivalent value of an evicted entry to its original purchaser.
- Consider making per-`(chain,app)` capacity resistant to spam by charging a minimum tier value proportional to slots consumed, so cheap repeated purchases cannot cheaply displace higher-value entries.

### Proof of Concept
1. Registered `BandwidthManager` on source chain `S`; victim app `V` legitimately calls `purchase(V, TierFour, 1, "EVM-8453")`, paying $1000, and `pallet-bandwidth` appends a `Subscription{tier: TierFour, remaining_bytes: 8MB, ...}` to `Allowance[EVM-8453][V]` (list currently has this 1 entry) [5](#0-4) .
2. Attacker `A` (unrelated address) calls `purchase(V, TierOne, 1, "EVM-8453")` 1023 times, each paying $50 for a small subscription targeting the *same* victim `app = V`. The list fills to `MAX_SUBSCRIPTIONS = 1024` [12](#0-11) .
3. Attacker calls `purchase(V, TierOne, 1, "EVM-8453")` one more time. `push_subscription` sees the list at cap, evicts index `0` — the victim's original TierFour, 8MB subscription — and emits `SubscriptionEvicted` with the lost 8MB, while the attacker's cheap entry is appended [13](#0-12) .
4. Victim `V`'s 8MB/$1000 paid allowance is now gone with no compensation, for an attacker cost of ~$51,250 total (or as little as one additional $50 purchase if the queue was already near-full from organic traffic), destroying value disproportionate to spend and leaving `V` either unable to dispatch messages (`GateError::NoAllowance`) or forced to repurchase.

### Citations

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

**File:** modules/pallets/bandwidth/src/lib.rs (L400-434)
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

**File:** evm/src/apps/BandwidthManager.sol (L138-181)
```text
    /// @notice Pay for `months` of `tier` bandwidth on `chain` for `app`.
    /// @dev Pulls the scaled tier price from `msg.sender` in the host's
    /// fee token, then dispatches a `BandwidthPurchaseMsg` to
    /// `pallet-bandwidth` on hyperbridge. The pallet credits an
    /// `(chain, app)` bucket bounded by tier `bytes` × `months`.
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
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L18-27)
```text
### Plans at a glance

| Plan   | Bytes  | $/byte    |
| ------ | ------ | --------- |
| $50    | 100 KB | $0.000500 |
| $100   | 300 KB | $0.000333 |
| $250   | 1 MB   | $0.000250 |
| $1000  | 8 MB   | $0.000125 |

Larger tiers trade upfront commitment for a steep per-byte discount — the $1000 plan is roughly 4× cheaper per byte than the $50 plan.
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L108-114)
```text
## Sponsorship

The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
```
