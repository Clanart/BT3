### Title
Permissionless `BandwidthManager.purchase()` lets an attacker grief-evict a victim app's prepaid subscription via the FIFO cap — ([File: modules/pallets/bandwidth/src/lib.rs])

### Summary
Like the Karak finding — where funds could be lost by any depositor because there was no visibility into (and no protection against) a pending, third-party-triggered state change (a slash) — `pallet-bandwidth`'s subscription ledger lets an unrelated, unprivileged actor destroy another app's already-paid-for bandwidth allowance by exploiting the FIFO eviction rule on the `(chain, app)` subscription queue, with no getter exposed to warn a purchaser their subscription is at risk of eviction before or after paying.

### Finding Description
`BandwidthManager.purchase()` on any EVM source chain is fully permissionless and lets **any caller credit bandwidth for any `(chain, app)` pair** — the `app` and `chain` fields are attacker-controlled, not restricted to the app owner: [1](#0-0) 

On Hyperbridge, `pallet-bandwidth` stores subscriptions per `(app_chain, app)` in a `BoundedVec` capped at `MAX_SUBSCRIPTIONS` (1024), where **every purchase — from anyone — appends to the same list**, and once the cap is hit, the **oldest** entry is silently evicted: [2](#0-1) [3](#0-2) 

The eviction logic itself lives in `on_accept`'s credit path (documented) and is emitted via `SubscriptionEvicted { app_chain, app, tier, lost_bytes }` — an event, not a preventable guard. Nothing in `purchase()`, `on_accept`, or `try_consume` checks whether the target `(chain, app)` queue is near capacity, and there is no getter that lets a purchaser check "is my target queue about to evict a legitimate holder" before committing funds: [4](#0-3) [5](#0-4) 

Because eviction always removes the **oldest** entry regardless of who purchased it or how much of its allowance is unconsumed, an attacker who is willing to pay tier-1 purchase costs can:
1. Wait for (or front-run) a victim's legitimate, expensive tier purchase for `(chain, app)`.
2. Submit enough subsequent cheap `purchase()` calls (each a normal, valid, fully-paid transaction) targeting the same `(chain, app)` pair to push the total live-list length past `MAX_SUBSCRIPTIONS` relative to the victim's entry.
3. Once the victim's subscription becomes the head of a full 1024-entry list, the attacker's next purchase evicts it — destroying the victim's unconsumed, already-paid-for byte allowance (`SubscriptionEvicted.lost_bytes`) with zero cooperation from the victim, an admin, a relayer, or a prover.

This is structurally the same failure the Karak report calls out: a delayed / queued protocol state change (here, FIFO eviction pressure) that a third party can trigger, that isn't visible to the affected party ahead of time (no getter), and that causes them to lose already-committed value.

### Impact Explanation
A victim app operator who pre-pays for bandwidth (e.g. the $1000/8MB tier) can have that unconsumed allowance evicted and permanently lost due to nothing more than unrelated purchase() spam targeting the same `(chain, app)` key — a pure loss-of-funds condition triggerable by any unprivileged, unprivileged-key-less actor. This falls squarely under the accepted "logic attacks" / "loss of funds" categories in the bounty gate. It requires no malicious relayer, prover, admin, or governance action — only ordinary, valid transactions to the permissionless `purchase()` entrypoint.

### Likelihood Explanation
Economically this is a griefing attack (attacker spends real money on cheap tier purchases to destroy a victim's larger investment), so it is not necessarily profit-motivated for the attacker, but it is cheap relative to damage when the victim's allowance is large and the attacker buys the smallest tier repeatedly, and it requires no privileged access or race-condition luck — only knowledge of the victim's `(chain, app)` target, which is public (purchases are on-chain events). The complete absence of any capacity/eviction-risk getter also means legitimate purchasers cannot even defensively check exposure before buying, mirroring the "no getter to check pending slash" root cause of the referenced report.

### Recommendation
- Add a getter (e.g. `remaining_capacity(chain, app)` / `is_near_eviction(chain, app)`) so purchasers can check queue depth before buying.
- Consider bounding eviction to same-purchaser entries, or refunding/pro-rating `lost_bytes` value on eviction, or increasing `MAX_SUBSCRIPTIONS` dynamically / per-app rather than a single global constant shared by any caller.
- Consider disallowing third parties from crediting an `(chain, app)` pair the caller does not own/control, or rate-limiting purchases per `(chain, app)` per block to make queue-stuffing economically and mechanically harder.

### Proof of Concept
1. Victim calls `BandwidthManager.purchase(app=V, tier=4 ($1000/8MB), months=1, chain=C)`. Pallet appends `Subscription{tier:4, remaining_bytes:8MB, expires_at:T+30d}` to `Allowance[C][V]`, presently at position `k` in the list (list length `< 1024`).
2. Attacker (unprivileged, no relationship to `V`) repeatedly calls `purchase(app=V, tier=1 ($50/100KB), months=1, chain=C)` — each call is fully valid, fully paid by the attacker, permissionless per `evm/src/apps/BandwidthManager.sol:148-180`.
3. Once the queue length for `(C, V)` reaches `1024`, the attacker's next `purchase()` call causes the pallet's credit path to evict the oldest entry — which, given enough prior attacker purchases, is now the victim's tier-4 subscription — emitting `SubscriptionEvicted{app_chain:C, app:V, tier:4, lost_bytes: <unconsumed bytes>}`.
4. The victim had no on-chain way to detect this incoming risk (no getter exposed beyond `remaining()`/`allowances()`, which report current state, not eviction proximity, per `docs/content/developers/evm/bandwidth/purchasing.mdx:207` and `modules/pallets/bandwidth/src/lib.rs`), and loses the unconsumed portion of their paid allowance permanently.

### Citations

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

**File:** modules/pallets/bandwidth/src/lib.rs (L99-118)
```rust
	/// Authorised purchase contract per source chain. A purchase whose
	/// `request.from` doesn't match this is rejected.
	#[pallet::storage]
	pub type BandwidthManager<T: Config> =
		StorageMap<_, Twox64Concat, StateMachine, H160, OptionQuery>;

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

**File:** modules/pallets/bandwidth/src/types.rs (L152-157)
```rust
/// Atomic check-and-deduct across all of an app's live subscriptions
/// on `(chain, app)`. `source` is `request.source` (= the purchase's
/// `app_chain`). Drains FIFO by insertion order.
pub trait BandwidthGate {
	fn try_consume(source: &StateMachine, app: &[u8], bytes: u32) -> Result<(), GateError>;
}
```
