### Title
Missing Deadline Guard in `addLiquidityExactShares` Allows Stale Liquidity Deposits at Unfavorable Prices — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`addLiquidityExactShares` (both overloads) in `MetricOmmPoolLiquidityAdder` accepts no `deadline` parameter and performs no cursor-bounds validation before executing a liquidity deposit. A transaction can sit in the mempool indefinitely and execute after the pool price has moved significantly, causing the user to deposit tokens into bins that are now far from the active price — resulting in an immediately impaired LP position worth less than the deposited principal.

---

### Finding Description

Every swap entry point in `MetricOmmSimpleRouter` calls `_checkDeadline(params.deadline)` before touching the pool. [1](#0-0) 

The `addLiquidityWeighted` overloads in `MetricOmmPoolLiquidityAdder` partially compensate with `_validateBinAndBinPosition`, which reads the live pool cursor and reverts if it has drifted outside caller-supplied bounds. [2](#0-1) 

Neither `addLiquidityExactShares` overload has a deadline parameter or any cursor-bounds check:

```solidity
function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
}
``` [3](#0-2) 

The only guards present are `_validateOwner`, `_validateDeltas`, and the `maxAmount` caps enforced inside the callback. [4](#0-3)  The `maxAmount` caps prevent overpaying but do not prevent depositing at the wrong price point — they only bound the quantity, not the timing or price context.

---

### Impact Explanation

In a bin-based AMM, liquidity is deposited into specific bins identified by `binIdxs`. If the pool price moves between transaction submission and execution:

1. The user's chosen `binIdxs` may now be far from the active bin.
2. Tokens deposited into out-of-range bins earn no fees and are priced at the bin's fixed exchange rate, not the current market rate.
3. The resulting LP position is immediately worth less than the deposited tokens at current market prices — a direct loss of user principal with no recourse.

The `maxAmountToken0`/`maxAmountToken1` caps do not mitigate this: they only prevent the pool from pulling *more* than the user authorized, not from pulling the full authorized amount at a stale price.

**Severity: Medium** — direct loss of user principal; no privileged actor required; loss magnitude scales with price movement during mempool delay.

---

### Likelihood Explanation

- Any user calling `addLiquidityExactShares` during periods of network congestion or gas price spikes is exposed.
- A MEV searcher can deliberately delay inclusion of a pending liquidity transaction until after a large price-moving swap, then include both in the same block — the liquidity lands at the post-swap price while the user's `maxAmount` caps are still satisfied.
- No special permissions or malicious setup are required; the trigger is a normal user transaction.

---

### Recommendation

Add a `deadline` parameter to both `addLiquidityExactShares` overloads and enforce it with the same `_checkDeadline` helper already used by the router:

```solidity
function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    uint256 deadline,          // ← add
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _checkDeadline(deadline);  // ← add
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(...);
}
```

Alternatively, expose the `minimalCurBin`/`maximalCurBin` cursor-bounds parameters (already present in `addLiquidityWeighted`) so callers can bound acceptable pool state without a wall-clock deadline.

---

### Proof of Concept

1. Pool is at bin 0, price = 1.00.
2. Alice calls `addLiquidityExactShares` targeting bins `[-1, 0, 1]` with `maxAmount0 = 1000e18`, `maxAmount1 = 1000e18`. Transaction enters mempool.
3. Bob executes a large swap that moves the pool to bin 5, price = 1.50. Alice's transaction is still pending.
4. MEV searcher includes Alice's transaction after Bob's swap in the same block.
5. Alice's tokens are deposited into bins `[-1, 0, 1]`, which are now 4–6 bins below the active price.
6. At current market rates, Alice's LP position (priced at the bin-1 exchange rate ≈ 1.00) is worth ~33% less than the 1.50-priced tokens she deposited — a direct principal loss with no deadline revert to protect her. [3](#0-2) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L68-68)
```text
    _checkDeadline(params.deadline);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L104-104)
```text
    _validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L165-167)
```text
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
  }
```
