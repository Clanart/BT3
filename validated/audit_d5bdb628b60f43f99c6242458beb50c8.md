### Title
Missing Deadline Parameter in All `addLiquidity*` Functions Allows Stale Execution at Adverse Pool Prices — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` exposes four public `addLiquidity*` entry points, none of which accept or enforce a deadline. A pending transaction can be held in the mempool and executed arbitrarily late, after the oracle-driven pool price has moved, causing the LP to deposit into bins at prices they never intended to accept.

---

### Finding Description

`MetricOmmSimpleRouter` correctly guards every swap entry point with `_checkDeadline(params.deadline)`: [1](#0-0) 

`MetricOmmPoolLiquidityAdder`, however, has no equivalent guard. All four public entry points — both overloads of `addLiquidityExactShares` and both overloads of `addLiquidityWeighted` — accept no `deadline` argument and perform no timestamp check before calling into the pool: [2](#0-1) [3](#0-2) [4](#0-3) 

The `addLiquidityExactShares` overloads are the most exposed: the user specifies exact bin indices and share amounts, but no cursor-bounds check is performed. The `addLiquidityWeighted` overloads do validate `minimalCurBin`/`maximalCurBin` at call time, but that check runs against the *delayed* pool state, not the state the user observed when signing — so it provides no protection against the stale-execution scenario.

The only slippage guard present is `maxAmountToken0`/`maxAmountToken1`, which caps token *quantity* but does not protect against *price* or *bin-position* staleness. [5](#0-4) 

---

### Impact Explanation

Because Metric OMM pools are oracle-driven, the active bin and price can shift substantially between the block where the user signed the transaction and the block where a MEV bot or validator eventually includes it. When `addLiquidityExactShares` executes late:

1. The user's shares are deposited into the bins they specified, which may now be far from the current oracle price.
2. Liquidity in out-of-range bins is immediately subject to adverse selection: the next swap that crosses those bins will extract value from the LP at the stale price.
3. The LP suffers immediate, quantifiable impermanent loss proportional to the price movement during the delay — a direct loss of deposited principal value.

The `maxAmountToken0`/`maxAmountToken1` caps bound *how many tokens* are pulled, but not *at what price* they are deployed, so they do not mitigate this loss.

---

### Likelihood Explanation

Any `addLiquidity` transaction that is submitted with insufficient gas, during network congestion, or that is deliberately withheld by a searcher can be replayed at a later block. This is an unprivileged, permissionless trigger requiring no special access. The Metric OMM oracle price updates continuously, so even moderate delays (minutes to hours) can produce meaningful price divergence.

---

### Recommendation

Add a `uint256 deadline` parameter to all four `addLiquidity*` entry points and call `_checkDeadline(deadline)` (or an equivalent inline check) before any state-modifying pool interaction, mirroring the pattern already used in `MetricOmmSwapRouterBase`:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol
function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
+   uint256 deadline,
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
+   if (block.timestamp > deadline) revert TransactionExpired(deadline, block.timestamp);
    _validateOwner(owner);
    ...
}
```

Apply the same change to both `addLiquidityWeighted` overloads. The `IMetricOmmPoolLiquidityAdder` interface and any off-chain integrations must be updated accordingly.

---

### Proof of Concept

1. Alice calls `addLiquidityExactShares` targeting bin index `0` (current active bin) with `maxAmountToken0 = 1000e18`, `maxAmountToken1 = 1000e18`. The oracle price at submission is $P_0$.
2. The transaction sits in the mempool for 30 minutes. The oracle price moves to $P_1 = 1.10 \times P_0$ (10% increase).
3. A MEV bot detects the pending transaction and includes it in a block after the price move.
4. `addLiquidityExactShares` executes: Alice's tokens are deposited into bin `0`, which is now *below* the current active bin. Her entire deposit is in token0 (the cheaper leg), and the pool immediately routes the next sell-token0 swap through her bin at the stale $P_0$ price.
5. Alice's effective entry price is 10% worse than the market price at execution time. She has lost ~10% of her deposited value relative to simply holding the tokens — a direct, quantifiable loss of principal with no recourse. [6](#0-5)

### Citations

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L91-94)
```text
  function _checkDeadline(uint256 deadline) internal view {
    // forge-lint: disable-next-line(block-timestamp)
    if (block.timestamp > deadline) revert IMetricOmmSimpleRouter.TransactionExpired(deadline, block.timestamp);
  }
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L71-81)
```text
  function addLiquidityExactShares(
    address pool,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateDeltas(deltas);
    return _addLiquidity(pool, msg.sender, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L88-116)
```text
  function addLiquidityWeighted(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(weightDeltas);
    _validatePositiveWeights(weightDeltas);
    _validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);

    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-167)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
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
