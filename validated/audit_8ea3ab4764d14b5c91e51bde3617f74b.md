### Title
Unprivileged griefing eviction of paid bandwidth subscriptions via unbounded `purchase()` targeting arbitrary `(chain, app)` - (File: modules/pallets/bandwidth/src/lib.rs, evm/src/apps/BandwidthManager.sol)

### Summary
The Sherlock report's core broken invariant is: a hard cap on the number of entries in a per-owner collection forces eviction/loss of a legitimately-held entry, and the entry point that manages that collection can be raced/abused by any party to permanently occupy or destroy a slot belonging to someone else. The same shape exists in Hyperbridge's `pallet-bandwidth` FIFO subscription ledger: `BandwidthManager.purchase()` is a fully public, unauthenticated entrypoint that lets **anyone** credit bandwidth to **any** `(chain, app)` pair by supplying only the cheapest configured tier, and the pallet's `push_subscription` evicts the **oldest** entry once the per-`(chain, app)` list hits `MAX_SUBSCRIPTIONS` (1024), regardless of the evicted entry's remaining value.

### Finding Description
`purchase()` in `evm/src/apps/BandwidthManager.sol` (lines ~148-193) takes an arbitrary `app` and `chain` parameter supplied entirely by the caller: [1](#0-0) 

There is no check that `msg.sender` is related to `app` in any way — any address can pay for and credit bandwidth to any victim `(chain, app)` pair, including the cheapest configured tier (`TierOne`) with `months = 1`.

On the pallet side, `push_subscription` (`modules/pallets/bandwidth/src/lib.rs`, ~400-437) appends every purchase — regardless of tier size — to the same bounded FIFO list keyed by `(app_chain, app)`, and once the list is at the `MAX_SUBSCRIPTIONS` cap (1024), the **oldest** entry is unconditionally evicted, destroying its remaining unused bytes: [2](#0-1) 

Because every subscription — cheap or expensive — occupies exactly one FIFO slot irrespective of its `bytes`/`remaining_bytes` size, an attacker can force-evict a victim's high-value subscription (e.g. a large `TierFour` allowance with months of remaining byte budget) by simply issuing 1024 cheap `TierOne` purchases against the same `(chain, app)` key. This is the direct analog of the Convergence delegation cap: a fixed slot limit that any party can fill from the outside, permanently destroying a value-bearing entry that the rightful holder cannot protect or selectively remove — `pallet-bandwidth` exposes no "clean one entry" or "raise my priority" mechanism, and unlike the delegation case there isn't even an owner-side permission check preventing a third party from writing into the victim's bucket in the first place (the delegation bug at least required the victim's own actions to hit the cap; here an attacker alone can trigger the eviction of value they never contributed).

Existing guards do not stop this: `try_consume`'s "no mutation on insufficient" property only protects against gate-side races, not against `push_subscription`'s unconditional eviction; and `is_purchase_message`/manager-authentication only verifies the *sender contract* is the registered `BandwidthManager` for the source chain, not that the caller of `purchase()` is authorized for the target `app`.

### Impact Explanation
Bandwidth allowances are a prepaid, fungible asset — `SubscriptionEvicted` explicitly reports `lost_bytes` as an on-chain-auditable loss. An attacker can permanently destroy an app's paid, unexpired bandwidth allowance (worth real fee-token value, since prices are non-zero: `UnknownTier` is returned for `price18d == 0`) for the cost of 1024 cheap `TierOne` purchases, which is dramatically cheaper than the value of a victim's high tier subscription(s) being evicted, especially against apps sponsoring bandwidth across many chains from a single treasury as documented. This is a loss-of-funds / logic attack against the legitimate beneficiary of a prepaid balance, executable by any unprivileged, ungoverned actor — squarely within the bounty's "stealing or loss of funds" / "logic attacks" category.

### Likelihood Explanation
The attack requires no privileged role, no relayer/prover/admin collusion, and no race against another user's transaction — it is a straightforward, repeatable call to a fully public function (`purchase()`) with attacker-chosen, cheap parameters. The only cost is 1024 `TierOne` purchases' worth of fee tokens plus gas/dispatch overhead, which is bounded and can be made arbitrarily cheap relative to the value destroyed if governance ever sets a low `TierOne` price (a very likely operational configuration since `TierOne` is designed to be the entry-level SKU). This makes the attack economically realistic against any high-value target `(chain, app)`.

### Recommendation
- Scope `purchase()` credit destinations: either require `msg.sender == app` (or an authorization mapping) for non-sponsored purchases, or separate the "self" and "sponsor" purchase paths so a stranger cannot silently write into an arbitrary victim's bucket.
- On the pallet side, evict based on remaining value rather than pure insertion order (e.g. evict the entry with the least `remaining_bytes`, or reject additional inserts once the list is full instead of silently destroying value), and/or size the eviction cost proportionally so a cheap subscription cannot evict a strictly more valuable one.
- Consider per-purchaser rate limiting or a minimum tier-price floor enforced pallet-side to prevent the cap from being filled cheaply.

### Proof of Concept
1. Governance configures `TierOne` with a low price (e.g. minimal viable SKU) and `TierFour` with a much larger byte budget/price, as documented in the tier model.
2. Victim app legitimately buys a `TierFour` subscription for `(chain=EVM-8453, app=VictimApp)`, crediting a large `remaining_bytes` allowance with months of validity — recorded at some position `k` in the FIFO list for that `(chain, app)` key.
3. Attacker calls `BandwidthManager.purchase(app = VictimApp_bytes, tier = TierOne, months = 1, chain = "EVM-8453")` 1024 times (using disposable EOAs or a single funded account since `purchase` has no per-caller restriction), each paying only the cheap `TierOne` price and each appending a new `Subscription` to the same `(chain, app)` FIFO list, per `push_subscription`: [3](#0-2) 
4. Once the list length reaches `MAX_SUBSCRIPTIONS` (1024), each subsequent purchase evicts the oldest entry; after enough insertions the victim's `TierFour` subscription (or any interior entry) reaches the head of the FIFO and is evicted, emitting `SubscriptionEvicted { app_chain, app, tier: TierFour, lost_bytes: <victim's remaining bytes> }` — the victim's paid, unexpired allowance is destroyed for an attacker cost far below the value lost.

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
