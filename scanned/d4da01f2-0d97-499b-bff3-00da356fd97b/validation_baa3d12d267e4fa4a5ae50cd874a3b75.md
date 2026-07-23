### Title
Missing Deadline Guard on All Liquidity-Adding Entry Points Allows Stale Transactions to Deposit at Unfavorable Oracle Prices - (File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol)

### Summary

`MetricOmmPoolLiquidityAdder` exposes four public liquidity-adding functions — two overloads of `addLiquidityExactShares` and two overloads of `addLiquidityWeighted` — none of which accept or enforce a `deadline` parameter. The sibling `MetricOmmSimpleRouter` enforces `_checkDeadline` on every swap entry point. The omission means a pending liquidity transaction can sit in the mempool and execute arbitrarily late, at an oracle price the user never intended to accept.

### Finding Description

`MetricOmmSwapRouterBase._checkDeadline` is defined as:

```solidity
// metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol:91-94
function _checkDeadline(uint256 deadline) internal view {
    if (block.timestamp > deadline)
        revert IMetricOmmSimpleRouter.TransactionExpired(deadline, block.timestamp);
}
``` [1](#0-0) 

Every swap function in `MetricOmmSimpleRouter` calls this guard before touching the pool: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

`MetricOmmPoolLiquidityAdder`, however, inherits only from `PeripheryPayments` and carries no deadline logic. Its four public entry points are:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol:56-81
function addLiquidityExactShares(address pool, address owner, uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0, uint256 maxAmountToken1,
    bytes calldata extensionData) external payable ...

function addLiquidityExactShares(address pool, uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0, uint256 maxAmountToken1,
    bytes calldata extensionData) external payable ...
``` [6](#0-5) 

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol:88-149
function addLiquidityWeighted(address pool, address owner, uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0, uint256 maxAmountToken1,
    int8 minimalCurBin, uint104 minimalPosition,
    int8 maximalCurBin, uint104 maximalPosition,
    bytes calldata extensionData) external payable ...
``` [7](#0-6) 

None of these functions accept a `deadline` argument or call any timestamp guard before invoking `IMetricOmmPoolActions.addLiquidity`.

The `addLiquidityWeighted` overloads include cursor-position bounds (`minimalCurBin` / `maximalCurBin`), but these check the pool's internal bin cursor, not the oracle price. A transaction can pass the cursor check while the oracle price has moved substantially within the same bin range. `addLiquidityExactShares` has no such secondary guard at all.

### Impact Explanation

Metric OMM is an oracle-anchored market maker: `MetricOmmPool.swap` fetches `getBidAndAskPrice` from the live oracle at execution time. Liquidity is deposited into discrete bins whose token composition is determined by the oracle price at the moment `addLiquidity` is called. If the oracle price has moved since the user signed the transaction:

- **`addLiquidityExactShares`**: the user's specified shares are deposited at the current (stale-relative-to-intent) oracle price. The token amounts pulled from the user are bounded by `maxAmountToken0`/`maxAmountToken1`, but the *ratio* at which those tokens are consumed is dictated by the live oracle. The user can end up depositing entirely into single-sided bins (e.g., all token0 into bins far above the current oracle price) that will not generate fees until the oracle price recovers.

- **`addLiquidityWeighted`**: the probe step determines `need0`/`need1` at the current oracle price, scales shares accordingly, then executes the paying add. If the oracle price has moved since the user's intent, the probe reflects the new price, and the user's capital is deployed at that new price — potentially into bins that are immediately out-of-range.

In both cases the user's principal is locked in LP shares at an oracle price they did not consent to. Removing liquidity recovers the tokens, but at the cost of gas and any fees missed or impermanent-loss-equivalent incurred during the window.

### Likelihood Explanation

Network congestion, gas price spikes, or deliberate transaction withholding by a block builder can delay any pending transaction by multiple blocks. Metric OMM's oracle price can move between blocks (Pyth/Chainlink feeds update continuously). A user who submits a liquidity transaction during a volatile period faces a realistic probability of execution at a materially different price. No privileged role is required; any ordinary user is exposed.

### Recommendation

Add a `uint256 deadline` parameter to all four public entry points in `MetricOmmPoolLiquidityAdder` and call `_checkDeadline` (or an equivalent inline check) as the first statement, mirroring the pattern already used in `MetricOmmSimpleRouter`:

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
    if (block.timestamp > deadline) revert TransactionExpired(deadline, block.timestamp);
    _validateOwner(owner);
    ...
}
```

Apply the same change to both `addLiquidityWeighted` overloads. Update `IMetricOmmPoolLiquidityAdder` accordingly.

### Proof of Concept

1. Alice calls `addLiquidityExactShares` targeting bin index 4 (above current oracle price) with `maxAmountToken0 = 1000e18`, `maxAmountToken1 = 0`. The oracle price at submission time is P.
2. The transaction sits in the mempool. The oracle price drops to P/2 (e.g., a Pyth update is pushed on-chain).
3. The transaction is included. At P/2, bin 4 is now far above the oracle price; the pool accepts Alice's 1000e18 token0 into that bin as a fully single-sided deposit.
4. Alice's 1000e18 token0 is locked in a bin that will not be traversed by swaps until the oracle price rises back above P. She receives no fees and cannot recover her tokens without removing liquidity (paying gas again).
5. Had a deadline of `block.timestamp + 60` been enforced, the transaction would have reverted at step 3, protecting Alice's capital. [8](#0-7) [9](#0-8)

### Citations

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L91-94)
```text
  function _checkDeadline(uint256 deadline) internal view {
    // forge-lint: disable-next-line(block-timestamp)
    if (block.timestamp > deadline) revert IMetricOmmSimpleRouter.TransactionExpired(deadline, block.timestamp);
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-68)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-93)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-131)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-155)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-81)
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

  /// @notice Add liquidity with explicit per-bin shares for `msg.sender`.
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
