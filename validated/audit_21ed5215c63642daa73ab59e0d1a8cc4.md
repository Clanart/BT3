### Title
Off-by-one in `priceGuard` boundary check allows circuit-broken oracle prices to reach pool swaps — (`smart-contracts-poc/contracts/PriceProvider.sol` and `smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

Both `PriceProvider` and `AnchoredPriceProvider` implement a `priceGuard` check that is supposed to halt quoting when the oracle reports a clamped/circuit-broken price. The check uses strict inequalities (`<` and `>`), so when the oracle clamps its output to exactly `guardMin` (the canonical circuit-breaker event), the condition is always `false` and the guard never fires. The pool receives and executes swaps at the wrong, clamped price.

---

### Finding Description

`setPriceGuard` in `OracleBase.sol` stores a `(min, max)` band for each feed. The purpose is identical to Chainlink's `minAnswer`/`maxAnswer` circuit-breaker: when an asset crashes, the oracle cannot report below `guardMin`, so it clamps its output to exactly `guardMin`. The consuming provider is supposed to detect this and halt.

In `PriceProvider._getBidAndAskPrice()`:

```solidity
// smart-contracts-poc/contracts/PriceProvider.sol  line 210
if (mid < guardMin || mid > guardMax) {
    return (0, type(uint128).max);
}
```

In `AnchoredPriceProvider._readLeg()`:

```solidity
// smart-contracts-poc/contracts/AnchoredPriceProvider.sol  line 292
if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);
```

Because the oracle's own clamping logic guarantees `mid >= guardMin` at all times (it cannot report below its minimum), the condition `mid < guardMin` is structurally unreachable during a real crash. The only reachable signal is `mid == guardMin`, which the strict `<` silently passes. The guard is therefore dead code for the exact scenario it was designed to catch.

The `setPriceGuard` setter enforces `minPrice < maxPrice` (strict), confirming the boundary values themselves are meaningful and intended to be inclusive trip-points. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

When an asset price collapses below `guardMin`, the oracle clamps its reported `mid` to exactly `guardMin`. Both providers pass this value through the guard unchanged and compute a bid/ask from it. The pool executes swaps at the clamped (artificially high) price. Counterparties who sell the crashing asset receive more of the other token than the true market price warrants, draining the pool's reserves of the healthy asset — a direct loss of LP principal matching the Venus/Blizz Finance pattern cited in the external report. [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation

Any flash-crash or de-peg event on a feed that has a `priceGuard` configured triggers this silently. No privileged action is required; any user can call `exactInputSingle` / `exactOutputSingle` on the router during the crash window. The `priceGuard` feature is explicitly provided and documented as a safety mechanism, so operators are expected to configure it, making the failure surface real and not hypothetical. [3](#0-2) 

---

### Recommendation

Change both guard checks from strict to inclusive inequalities, mirroring the fix recommended in the external report:

```diff
// PriceProvider.sol
- if (mid < guardMin || mid > guardMax) {
+ if (mid <= guardMin || mid >= guardMax) {
      return (0, type(uint128).max);
  }

// AnchoredPriceProvider.sol
- if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);
+ if (mid <= guardMin || mid >= guardMax) return (mid, spreadBps, refTime, false);
```

Also update `setPriceGuard` to enforce `minPrice < maxPrice` remains valid under the new semantics (it already does, since a price exactly equal to `guardMin` is now treated as a circuit-break, not a valid quote). [6](#0-5) [7](#0-6) 

---

### Proof of Concept

1. Admin calls `setPriceGuard(feedId, 1_000_000, 1_000_000_000_000)` on the oracle — a realistic lower bound for a token priced at $0.01 (8 decimals).
2. The token crashes; the oracle clamps its output to exactly `mid = 1_000_000`.
3. `PriceProvider._getBidAndAskPrice()` evaluates `1_000_000 < 1_000_000` → `false`; guard does not fire.
4. `_getBidAskFrom(1_000_000, adjustedSpread)` computes a bid/ask from the clamped price.
5. The pool executes swaps at the clamped price; sellers of the crashed token drain the pool's healthy-token reserves.
6. LPs suffer direct principal loss proportional to the gap between `guardMin` and the true market price. [8](#0-7) [9](#0-8)

### Citations

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L191-231)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
        }

        // 3. Basic validity — price must be positive, spread must not be stalled marker
        if (mid == 0 || spread >= ORACLE_BPS) {
            return (0, type(uint128).max);
        }

        // 4. Price guard check (moved from oracle)
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) {
            return (0, type(uint128).max);
        }

        // 5. Compute bid/ask from mid + confidence-adjusted spread
        //    confidenceParam multiplies oracle spread; 0 means no spread
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);

        // 6. Apply marginStep adjustment
        (uint256 bidOut, bool bidOk) = _applyBidAdjustments(bid);
        if (!bidOk || bidOut > type(uint128).max) return (0, type(uint128).max);

        (uint256 askOut, bool askOk) = _applyAskAdjustments(ask);
        if (!askOk || askOut > type(uint128).max) return (0, type(uint128).max);

        // 7. Hard invariant: bid must be strictly less than ask.
        //    Can be violated when marginStep < 0 and confidence is too small.
        if (bidOut >= askOut) return (0, type(uint128).max);

        return (uint128(bidOut), uint128(askOut));
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-295)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

        // Basic validity — mid positive, spreadBps not the stalled/off-hours marker (the Chainlink oracle
        // writes spreadBps = ORACLE_BPS when an RWA market is closed).
        if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);

        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

        ok = true;
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L88-97)
```text
    function setPriceGuard(bytes32 feedId, uint128 minPrice, uint128 maxPrice)
        external
        checkRole(feedId)
    {
        require(minPrice < maxPrice);

        priceGuard[feedId] = PriceGuard({min: minPrice, max: maxPrice});

        emit PriceGuardUpdated(feedId, minPrice, maxPrice);
    }
```
