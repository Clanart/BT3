Confirmed: `purchase()` on `BandwidthManager.sol` is fully permissionless and takes an arbitrary `app` recipient — the caller only pays for the tier, but names *any* victim app to credit. This, combined with the pallet's fixed 1024-entry FIFO cap and eviction-by-eldest rule, is the local analog to the Axelar flow-limit DoS: a cheap, unprivileged, repeated action against a shared bounded resource destroys value that legitimate users already paid for.

### Title
Permissionless `purchase()` lets an attacker grief any app's paid bandwidth via FIFO-cap eviction - (File: `evm/src/apps/BandwidthManager.sol`, `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`BandwidthManager.purchase()` lets *anyone* credit *any* app's `(chain, app)` bandwidth bucket by supplying an arbitrary `app` bytes parameter — there is no check that `msg.sender` owns or controls the app being credited. [1](#0-0) 
On the pallet side, each `(app_chain, app)` bucket is a FIFO `BoundedVec` capped at 1024 subscriptions; pushing onto a full list evicts the **oldest** entry regardless of its remaining, unconsumed byte balance. [2](#0-1) [3](#0-2) 

### Finding Description
The pallet's `on_accept` only validates that the purchase message came from the registered `BandwidthManager` for the source chain (`request.from` matches) — it never validates that the payer (`msg.sender` on the EVM side) has any relationship to the `app` field being credited. The `app` field is attacker-controlled calldata forwarded verbatim through `BandwidthPurchaseMsg`. [4](#0-3) [5](#0-4) 

This means an attacker can repeatedly call `purchase()` naming a victim app they don't control, buying the cheapest configured tier (`TierOne`) each time. Each purchase appends a new `Subscription` row to the victim's FIFO list via `push_subscription`, and once the list hits the 1024-entry cap, further pushes silently evict the **oldest** entry — which, in the normal operating pattern described by the docs ("at the default of one purchase per cycle, 1024 buys is years of headroom"), is very likely to be a legitimate, still-unconsumed subscription that the victim app already paid real money for. [6](#0-5) [7](#0-6) 

This is structurally the same primitive as the Axelar flow-limit bug: a shared, per-key bounded resource (flow-limit capacity / FIFO subscription slots) can be consumed or churned by an unprivileged, low-cost, repeated action from an account that isn't the intended beneficiary, destroying availability/value for the legitimate party. Here the attacker doesn't even need large capital — because tier price is fixed and low for `TierOne`, spamming 1024 cheap purchases costs far less than the bytes/value the victim already locked in with a single large tier purchase (e.g. TierFour, "$1000 / 8 MB"), so the attacker can permanently burn a victim's large, expensive, unexpired allocation for a fraction of its cost, emitting `SubscriptionEvicted` with the lost bytes. [8](#0-7) [9](#0-8) 

### Impact Explanation
This causes real loss of purchased, paid-for value and denial of service to the victim app: their entire ISMP message-dispatch capability on that source chain is gated by `BandwidthGate::try_consume`, and if their outstanding (larger, expensive) subscriptions are evicted, they revert to `GateError::NoAllowance` / `Insufficient`, blocking all their cross-chain requests until they repurchase. [10](#0-9) 
This matches the "Impact Gate" criteria for loss of funds (paid bandwidth allowance destroyed) and unauthorized manipulation of another party's state (an unprivileged third party mutating a victim's storage entry it doesn't own).

### Likelihood Explanation
The attack requires no privileged role, no relayer/prover/governance collusion, and no front-running — it is a plain, repeated public call to `purchase()` with only the cheapest tier's price paid per iteration. It is cheap on L2s just like the original Axelar report notes for flow-limit spam. The only friction is the gas cost of ~1024 transactions and the cheapest-tier price × 1024, which for high-value victim tiers (e.g. $1000/8MB tier) can be dramatically less than the value destroyed.

### Recommendation
- Bind eligibility to credit an `(chain, app)` bucket to some app-controlled action (e.g., require an authorization signature/allow-list opt-in from the app before third parties can top it up), or
- Change eviction policy to evict the entry with the least remaining value/bytes (or shortest fee-weighted priority) instead of strictly FIFO-oldest, so unrelated cheap purchases cannot displace larger legitimate subscriptions, and/or
- Cap the number of subscriptions a single non-owning payer can push into another app's bucket per epoch, or require `min_bytes`/`min_tier` proportional to existing subscriptions before an eviction is allowed to touch entries funded by a different payer.

### Proof of Concept
1. Victim app buys `TierFour` (8 MB / $1000) once for its `(app_chain, app)` bucket via `purchase(app, TierFour, 1, chain)` — a `Subscription` with large `remaining_bytes` and long `expires_at` sits somewhere in the FIFO list. [11](#0-10) 
2. Attacker, controlling no relationship to `app`, calls `purchase(app, TierOne, 1, chain)` 1024+ times, each time naming the victim's exact `app` bytes and paying only the cheap `TierOne` price ($50/100KB). [12](#0-11) 
3. Once the victim's `(app_chain, app)` list is at the 1024 cap, each further attacker purchase evicts the oldest entry — eventually reaching and evicting the victim's TierFour subscription, deleting its unconsumed, paid-for bytes and firing `SubscriptionEvicted { lost_bytes }`. [9](#0-8) 
4. The victim's app is now left only with the attacker's tiny junk `TierOne` subscriptions (or none, if the attacker stops purchasing), and the next `try_consume` call for a normal-sized message returns `GateError::Insufficient`/`NoAllowance`, blocking the victim's legitimate cross-chain dispatch until they repurchase — at strictly worse economics than what they already paid for. [13](#0-12)

### Citations

**File:** evm/src/apps/BandwidthManager.sol (L29-40)
```text
/// Wire payload dispatched by `purchase()` to `pallet-bandwidth`. The
/// pallet credits a tier-bucket on `chain` for `app`, scaled by `months`.
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

**File:** evm/src/apps/BandwidthManager.sol (L148-193)
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

**File:** modules/pallets/bandwidth/src/types.rs (L95-111)
```rust
/// drains via the gate, `expires_at` is fixed at purchase time and
/// never extends. Repurchases append a new row instead of stacking.
#[derive(
	Encode, Decode, DecodeWithMemTracking, TypeInfo, MaxEncodedLen, Clone, PartialEq, Eq, Debug,
)]
pub struct Subscription {
	/// SKU this subscription was bought against; for analytics/events
	/// only — the gate doesn't look at it during drain.
	pub tier: TierIndex,
	/// Bytes left to spend. Decrements as the gate drains; the entry
	/// is popped once this hits zero.
	pub remaining_bytes: BandwidthBytes,
	/// Unix seconds. Gate sweeps entries where `expires_at <= now`.
	pub expires_at: u64,
	/// Unix seconds at insertion — fixes FIFO order under same-block buys.
	pub purchased_at: u64,
}
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

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L56-77)
```text
## Subscription Lifecycle

Each `(chain, app)` row holds a FIFO list of subscriptions, capped at **1024** entries. Every purchase appends a new subscription — same-tier repurchases don't stack, they queue.

A subscription is immutable across its lifetime:

| Field             | Behavior                                                                       |
| ----------------- | ------------------------------------------------------------------------------ |
| `tier`            | Recorded at purchase time. Used for events and analytics, not for gating.      |
| `remaining_bytes` | Drains as the gate consumes messages. Pops once it hits zero.                  |
| `expires_at`      | Fixed at purchase. Never extends — a repurchase is a _new_ row, not a renewal. |
| `purchased_at`    | Insertion timestamp. Fixes FIFO order under same-block buys.                   |

### Drain order

The gate drains from the **head** of the list. Once the head is fully consumed it pops and continues into the next. If the next entry is expired it's swept silently — what you paid for is yours only until it expires.

This matters when you queue multiple tiers: the cheapest/oldest entry is consumed first regardless of which tier it came from. Plan top-ups so a higher tier doesn't sit behind a soon-to-expire lower tier you'd rather burn last.

### Eviction

Pushing onto a full list (1024 entries) evicts the **oldest** entry and emits `SubscriptionEvicted` with the lost bytes so the loss is auditable on-chain. In practice this only happens under pathological repeat-buy behavior — at the default of one purchase per cycle, 1024 buys is years of headroom.
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L134-134)
```text
8. **Credit.** Pallet's `on_accept` checks `request.from` matches the registered manager, decodes the body, looks up `TierConfig`, computes `bytes × months` and `duration_secs × months`, and appends a fresh `Subscription` to the `(app_chain, app)` FIFO list. Emits `BandwidthCredited { app_chain, app, paid_from, tier, bytes, expires_at }`. If the list was at the 1024 cap, the oldest entry is evicted with `SubscriptionEvicted`.
```

**File:** modules/pallets/bandwidth/src/lib.rs (L161-175)
```rust
		ForceCredited {
			app_chain: StateMachine,
			app: AppKey,
			tier: TierIndex,
			bytes: BandwidthBytes,
			expires_at: u64,
		},
		/// The 1024-cap pushed out the oldest subscription. `lost_bytes`
		/// is what the user paid for and won't get to use.
		SubscriptionEvicted {
			app_chain: StateMachine,
			app: AppKey,
			tier: TierIndex,
			lost_bytes: BandwidthBytes,
		},
```

**File:** modules/pallets/bandwidth/src/lib.rs (L313-322)
```rust
				})
				.collect();

			let mut body = vec![ACTION_SET_TIERS];
			body.extend(rows.abi_encode_params());

			let commitment = Self::dispatch_governance(target, manager, body)?;
			Self::deposit_event(Event::TiersDispatched { target, count, commitment });
			Ok(())
		}
```

**File:** modules/pallets/bandwidth/src/lib.rs (L509-535)
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
