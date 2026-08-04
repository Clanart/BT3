## Analysis

The InfiniFi report's core broken invariant: **an unauthenticated, cheap public call can mutate state keyed to an arbitrary victim address, permanently destroying value the victim already paid for, via a bounded/evicting data structure the victim doesn't control.**

The closest local analog is in the bandwidth-purchase subsystem: `purchase()` on `BandwidthManager.sol` lets *anyone* pay for bandwidth on behalf of *any* `(chain, app)` pair, and `pallet-bandwidth`'s `push_subscription` stores subscriptions in a hard-capped (`1024`), FIFO `BoundedVec` that **silently evicts the oldest entry** whenever a new one is pushed — regardless of who paid for the evicted entry or the new one.### Title
Permissionless bandwidth `purchase()` lets anyone evict a victim app's paid, unexpired bandwidth via FIFO cap-griefing - ([File: modules/pallets/bandwidth/src/lib.rs])

### Summary
`BandwidthManager.purchase()` (`evm/src/apps/BandwidthManager.sol:148-193`) is a fully permissionless, public entrypoint: any caller may pay for bandwidth on behalf of **any** `(chain, app)` pair by supplying an arbitrary `app` and `chain` — this is the documented "sponsorship" feature. On the Hyperbridge side, `pallet-bandwidth::push_subscription` (`modules/pallets/bandwidth/src/lib.rs:400-437`) stores each `(app_chain, app)`'s subscriptions in a `BoundedVec` capped at `MAX_SUBSCRIPTIONS = 1024` (`modules/pallets/bandwidth/src/types.rs:19-22`), and **unconditionally evicts the oldest live entry** whenever a push would exceed the cap, regardless of who paid for the evicted subscription or the new one:

```rust
let evicted = Allowance::<T>::mutate(app_chain, app, |list| {
    let evicted = if list.len() == MAX_SUBSCRIPTIONS as usize {
        Some(list.remove(0))
    } else { None };
    let _ = list.try_push(new_sub);
    evicted
});
``` [1](#0-0) 

This is structurally identical to the InfiniFi bug class: an unauthenticated, low-privilege public function (`mint`/`purchase`) can be spammed against an **arbitrary victim address** to trigger a side-effect (`restrictActionUntil`/FIFO eviction) that destroys or blocks something the victim owns, with no allowlist or ownership check gating who may target whom.

### Finding Description
`purchase()` only validates that `app`/`chain` are non-empty and `months > 0`, and that the caller can pay the scaled tier price — it never checks that the caller is the owner of `app`, nor limits how many subscriptions one caller may push onto another's bucket:

```solidity
function purchase(bytes calldata app, uint256 tier, uint256 months, bytes calldata chain)
    external
    returns (bytes32 commitment)
{
    if (app.length == 0 || chain.length == 0 || months == 0) revert InvalidPurchase();
    ...
    IERC20(feeToken).safeTransferFrom(msg.sender, address(this), amount);
    ...
}
``` [2](#0-1) 

On the pallet side, `on_accept` (via `push_subscription`) appends every successful purchase message to the target `(app_chain, app)` FIFO list, and `SubscriptionEvicted` is emitted (audit-only, non-reversible) when the 1024 cap is hit:

```rust
if let Some(old) = evicted {
    Self::deposit_event(Event::SubscriptionEvicted {
        app_chain: *app_chain,
        app: app.clone(),
        tier: old.tier,
        lost_bytes: old.remaining_bytes,
    });
}
``` [3](#0-2) 

Because the FIFO is a shared resource per `(app_chain, app)` and any address can push into it via sponsorship, an attacker can repeatedly call `purchase()` targeting a victim's `(chain, app)` with the cheapest configured tier and `months = 1`. Once the attacker's cheap subscriptions fill the remaining headroom up to the 1024 cap, each subsequent attacker purchase evicts the **oldest live entry** — which will be the victim's own legitimately paid, unexpired, higher-value subscription — permanently destroying `lost_bytes` of prepaid bandwidth the victim already paid for and never got to consume. This exactly matches the "bandwidth balances must move exactly once and only to the rightful beneficiary and amount" invariant from the impact gate: the victim's paid bandwidth is destroyed with no compensation and no consent.

The existing guards do not stop this path:
- The gate's "no mutation on insufficient" property (`BandwidthGate::try_consume`) only protects against *draining* races, not against eviction via new pushes.
- The allowlist bypass is admin-only and does not protect an app's *existing purchased subscriptions* from eviction by third-party purchases.
- `is_purchase_message` only checks that `request.from` matches a registered manager per source chain — it does not restrict who behind that manager (`msg.sender` on the EVM side) is allowed to target a given `app`.

### Impact Explanation
An attacker can force `SubscriptionEvicted` against a targeted `(chain, app)`, destroying bandwidth the app already paid for and has not yet consumed. Once the victim's live subscriptions are exhausted (either evicted or naturally drained), the `BandwidthGate` rejects all further ISMP dispatches from that app until it repurchases — a denial-of-service on the app's cross-chain messaging that also constitutes loss of the previously paid, unspent funds represented by the evicted `lost_bytes`. This falls squarely under the accepted impact categories: loss of funds (destroyed prepaid bandwidth) and logic attack against bandwidth-balance accounting ("bandwidth balances must move exactly once and only to the rightful beneficiary and amount").

### Likelihood Explanation
Likelihood is **low-to-moderate**. The attack requires the attacker to fund enough purchases (at the cheapest configured tier) to reach the 1024-entry cap for the targeted `(chain, app)` before triggering eviction of the victim's real subscription — a real, non-trivial cost (potentially tens of thousands of dollars at documented tier pricing) unless the victim's app already has a near-full queue. However, unlike a typical DoS, this requires no privileged role, no relayer/prover collusion, and no front-running — any address can execute it purely through the public `purchase()` entrypoint, making it a viable griefing/sabotage vector against a specific high-value app when the attack cost is smaller than the value of the app's outage or the destroyed prepaid bandwidth (e.g., targeting an app that recently bought a large, long-duration tier while its queue is nearly full from routine repurchases).

### Recommendation
- Restrict eviction so that only subscriptions paid by the same payer (or an eviction-priority scheme, e.g. evict-lowest-remaining-value rather than strict FIFO) can be pushed out by third-party sponsorship purchases.
- Alternatively, decouple the sponsorship purchase cap from the app's own purchase cap — e.g., give third-party-sponsored subscriptions a smaller sub-cap, or require sponsorship purchases to be flagged and evicted before self-funded ones.
- Consider raising `MAX_SUBSCRIPTIONS` eviction to require confirmation/consent from the app or add a per-app minimum-notice/cooldown before an externally-funded purchase can evict an existing entry.
- Emit `SubscriptionEvicted` with the payer of the evicted entry vs. the payer of the pushing purchase, so off-chain monitoring can flag adversarial sponsorship patterns.

### Proof of Concept
1. Governance configures `TierOne` with a low per-month price (e.g., the documented `$50`/tier).
2. Victim app `V` on chain `C` already holds `N` live subscriptions in `Allowance::<T>::get(C, V)` (any number `< 1024`, including subscriptions with significant remaining `bytes`/`expires_at`).
3. Attacker, using any address, repeatedly calls `BandwidthManager.purchase(app = V, tier = TierOne, months = 1, chain = C)` from `evm/src/apps/BandwidthManager.sol:148`, paying the tier price each time (no ownership check on `app` is required — this is the documented sponsorship feature).
4. Each purchase dispatches a `BandwidthPurchaseMsg` to `pallet-bandwidth`, which calls `push_subscription` (`modules/pallets/bandwidth/src/lib.rs:404-437`), appending the attacker's subscription to `Allowance::<T>` for `(C, V)`.
5. After `1024 - N` attacker purchases, the list is at cap. The attacker's next purchase pushes the list over capacity, triggering `list.remove(0)` — evicting `V`'s oldest live (and possibly high-value, long-duration) subscription, emitting `SubscriptionEvicted { app_chain: C, app: V, lost_bytes }`.
6. `V`'s prepaid, unconsumed bandwidth (`lost_bytes`) is destroyed with no compensation; `V` never authorized or consented to being sponsored or having its queue manipulated by the attacker.

Note: I could not fully verify governance's default tier price configuration or whether any additional runtime-level rate-limiting on `purchase()` exists beyond what's shown in `BandwidthManager.sol` and `pallet-bandwidth/src/lib.rs` — the index does not contain deployment-specific tier price values, so exact attack cost is not concretely quantifiable from the code alone.

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L416-425)
```rust
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

**File:** modules/pallets/bandwidth/src/lib.rs (L427-434)
```rust
			if let Some(old) = evicted {
				Self::deposit_event(Event::SubscriptionEvicted {
					app_chain: *app_chain,
					app: app.clone(),
					tier: old.tier,
					lost_bytes: old.remaining_bytes,
				});
			}
```

**File:** evm/src/apps/BandwidthManager.sol (L148-163)
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
```
