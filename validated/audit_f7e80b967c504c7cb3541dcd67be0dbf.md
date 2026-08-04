### Title
Permissionless third-party `purchase()` calls let an attacker grief and permanently destroy another app's already-paid bandwidth allowance via FIFO eviction - (File: `evm/src/apps/BandwidthManager.sol`)

### Summary
The QuickSwap bug's core invariant break is: **an unprivileged third party can act on a victim's state via a public entrypoint that takes the victim's identity as an untrusted parameter, causing the victim to lose access to (or the value of) resources they already committed, at disproportionate cost to the attacker.** The local analog is `BandwidthManager.purchase()` in the pallet-bandwidth / BandwidthManager bridge accounting system: the `app` parameter is fully attacker-controlled and unauthenticated, and the destination ledger (`pallet-bandwidth`) stores subscriptions in a capped FIFO list that silently evicts the oldest entry once full. An attacker can repeatedly call `purchase()` naming a victim `app`, stuffing the victim's FIFO queue until it evicts the victim's own legitimately-paid, unconsumed subscription — permanently destroying bandwidth the victim already paid for.

### Finding Description
`BandwidthManager.purchase()` accepts an arbitrary `app` byte string with no check that `msg.sender` corresponds to, owns, or is authorized to act on behalf of `app`: [1](#0-0) 

Anyone can call this function naming any victim application and any credit `chain`, paying with their own funds: [2](#0-1) 

On the Hyperbridge side, `pallet-bandwidth` credits a `(chain, app)` bucket by appending to a FIFO list capped at 1024 entries; pushing onto a full list evicts the **oldest** entry regardless of its remaining unconsumed value: [3](#0-2) 

This eviction behavior is explicitly documented: pushing onto a full list evicts the oldest entry and emits `SubscriptionEvicted` with the lost bytes: [4](#0-3) 

Because `purchase()` never verifies that the caller is the `app` (or is authorized by it) — this is intentional to support the documented "sponsorship" feature where third parties can pay for other apps' bandwidth — an attacker can weaponize the same permissionless path used for legitimate sponsorship to instead grief the target: repeatedly calling `purchase()` for the victim's `(chain, app)` key with the cheapest configured tier and `months = 1` until the FIFO list reaches its 1024 cap and begins evicting the victim's older, larger, still-unconsumed subscription (e.g., a subscription the victim paid a premium tier for, which has not yet been drained by the gate).

This mirrors the reported bug class precisely: in the QuickSwap case, `mint(sender, recipient, ...)` let an attacker act on `recipient`'s liquidity position to reset `lastLiquidityAddTimestamp` and trap the victim in `liquidityCooldown` forever. Here, `purchase(app, ...)` lets an attacker act on `app`'s bandwidth ledger to force FIFO eviction and destroy the victim's already-paid allowance — in both cases a public entrypoint takes a *third party's identity* as an untrusted parameter and mutates state that gates the victim's access to their own resource.

### Impact Explanation
This falls squarely under the explicitly listed "bandwidth accounting" impact bucket: bridged/prepaid balances "must move exactly once and only to the rightful beneficiary." Here, a victim app's paid-for, undrained bandwidth allowance can be unilaterally destroyed by an unrelated third party through normal contract usage, with no admin, relayer, or prover compromise required. The victim's dispatches will begin failing with `GateError::NoAllowance`/`Insufficient` once their subscription is evicted, effectively causing loss of already-paid-for cross-chain messaging capacity — a direct funds/value loss.

### Likelihood Explanation
The attack requires no privileged role, no proof forgery, and no relayer/validator collusion — only repeated calls to a fully public function (`purchase`) naming the victim's `app`/`chain`. The attacker must fund 1024 cheap-tier purchases to force the eviction, which is a real but bounded and attacker-controllable cost (governance can configure very low-priced tiers, and the cap is fixed at 1024 regardless of tier). Because the docs themselves acknowledge eviction is "pathological" but do not prevent it, and because `purchase()`'s lack of `app`-ownership binding is by design (to support sponsorship), the vulnerable code path is reachable today without any additional preconditions.

### Recommendation
- Require that `force_credit`/eviction risk be mitigated by either: (a) restricting `purchase()` to require `msg.sender == app` or an explicit sponsorship allow-list per `(payer, app)` pair, decoupling "anyone can sponsor" from "anyone can flood," or
- Change the FIFO cap policy so eviction only removes fully-expired or fully-drained entries, and reject/queue new purchases (rather than silently evicting live, unconsumed subscriptions) when the list is full and all entries are still live, or
- Bound how many subscriptions a single non-owner payer can insert per `(chain, app)` within a time window, preventing a single actor from unilaterally filling another app's queue.

### Proof of Concept
1. Victim app `A` on chain `C` purchases a large tier (e.g., `TierFour`, big `bytes`/`duration_secs`) via `BandwidthManager.purchase(A, TierFour, 12, C)`. This subscription sits at the head/near the head of `A`'s FIFO list and is only partially drained.
2. Attacker, with no relationship to `A`, repeatedly calls `BandwidthManager.purchase(A, TierOne, 1, C)` 1024 times (or however many are needed to reach the cap given `A`'s current queue depth), paying the cheap `TierOne` price each time from their own wallet.
3. Once `A`'s `(C, A)` FIFO list hits the 1024-entry cap on the pallet side, each subsequent attacker purchase evicts the oldest entry per: [4](#0-3) 
4. If `A`'s legitimate `TierFour` subscription is old enough in the queue (i.e., has been sitting there because the gate hasn't fully drained it yet), it is evicted and its `remaining_bytes` are destroyed via `SubscriptionEvicted`, even though `A` already paid for and had not consumed them.
5. `A`'s subsequent ISMP dispatches through `BandwidthGate::try_consume` now fail with `NoAllowance`/`Insufficient` despite `A` having paid for bandwidth that should still be available — a direct loss of the value `A` purchased, caused entirely by an unauthenticated third party.

### Citations

**File:** evm/src/apps/BandwidthManager.sol (L138-163)
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
```

**File:** evm/src/apps/BandwidthManager.sol (L164-180)
```text

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

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L75-77)
```text
### Eviction

Pushing onto a full list (1024 entries) evicts the **oldest** entry and emits `SubscriptionEvicted` with the lost bytes so the loss is auditable on-chain. In practice this only happens under pathological repeat-buy behavior — at the default of one purchase per cycle, 1024 buys is years of headroom.
```
