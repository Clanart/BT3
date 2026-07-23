Audit Report

## Title
Missing Deadline on `addLiquidityExactShares` Allows Validators to Force Unfavorable Liquidity Composition — (`File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary

`MetricOmmPoolLiquidityAdder` exposes four `addLiquidity*` entry points, none of which accept or enforce a deadline. A malicious block producer can hold a pending `addLiquidityExactShares` transaction until the oracle price has moved enough that the user's specified bins are entirely out-of-range, forcing a single-token deposit at a price the user never intended, with immediate impermanent loss relative to their intended balanced position.

## Finding Description

`MetricOmmSwapRouterBase._checkDeadline` is correctly wired into every swap entry point in `MetricOmmSimpleRouter`: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

`MetricOmmPoolLiquidityAdder` has no equivalent guard. Both overloads of `addLiquidityExactShares` accept no `deadline` parameter and perform no timestamp check: [6](#0-5) [7](#0-6) 

`addLiquidityWeighted` has a partial mitigation: `_validateBinAndBinPosition` checks the pool cursor against caller-supplied `minimalCurBin`/`maximalCurBin` bounds at probe time, which can revert if the oracle has moved the cursor outside those bounds: [8](#0-7) [9](#0-8) 

`addLiquidityExactShares` has **no such protection at all**. It accepts explicit bin indices and shares, enforces only per-token amount caps (`maxAmountToken0`, `maxAmountToken1`), and calls directly into the pool: [10](#0-9) 

The amount caps do not prevent composition shift. They cap the maximum pull, but if the oracle price moves past all user-specified bins, the pool requests only one token (up to that token's cap) and zero of the other. The user receives a fully out-of-range, single-sided position: [11](#0-10) 

## Impact Explanation

A user who submits `addLiquidityExactShares` targeting bins straddling the current oracle price can have their transaction delayed by a malicious validator until the oracle price has moved past all their bins. At that point the pool pulls only one token (up to the user's cap for that token), the user's position is entirely out-of-range and earns no fees, and the user has immediate impermanent loss relative to the price at which they intended to deposit. This constitutes a direct loss of LP principal value relative to the user's intent, matching the "bad-price execution" and "direct loss of user principal" impact categories. [6](#0-5) 

## Likelihood Explanation

Any block producer (validator/sequencer) who observes a pending `addLiquidityExactShares` transaction in the mempool can delay inclusion until the oracle price has moved sufficiently. On chains with fast oracle updates (Pyth, Lazer) and active MEV infrastructure, the oracle price can move materially within seconds to minutes. The attack requires no special permissions and no malicious contract — only the ability to reorder or delay a single transaction. The pattern is identical to the well-known deadline-missing vulnerability class that motivated deadline parameters in Uniswap v2/v3 and that the same codebase already guards against in its swap router. [2](#0-1) 

## Recommendation

Add a `uint256 deadline` parameter to both overloads of `addLiquidityExactShares` (and both overloads of `addLiquidityWeighted`, even though they have partial cursor-bound protection) in `MetricOmmPoolLiquidityAdder` and call `_checkDeadline(deadline)` (or an equivalent inline `if (block.timestamp > deadline) revert ...` check) at the top of each function, mirroring the pattern already used in `MetricOmmSwapRouterBase._checkDeadline`. Update `IMetricOmmPoolLiquidityAdder` accordingly. [1](#0-0) [6](#0-5) 

## Proof of Concept

1. Oracle price for pool P is 1000 USDC/ETH. User constructs `addLiquidityExactShares` targeting bins [998–1000, 1000–1002] with `maxAmountToken0 = 1 ETH`, `maxAmountToken1 = 1000 USDC`, expecting a ~50/50 deposit.
2. User broadcasts the transaction. A malicious validator sees it in the mempool and withholds it.
3. Oracle price updates to 1005 USDC/ETH (both user bins are now below the oracle price).
4. Validator includes the transaction. The pool now requires only token1 (USDC) for both bins.
5. The callback in `metricOmmModifyLiquidityCallback` pulls up to 1000 USDC from the user and zero ETH; the `amount0Delta > max0 || amount1Delta > max1` check passes because `amount0Delta == 0 ≤ max0`.
6. The user holds a fully out-of-range, single-sided USDC position in bins [998–1002] while the market trades at 1005. They have immediate impermanent loss and earn no fees until the price returns below 1002.

Foundry test plan: deploy pool at price 1000, submit `addLiquidityExactShares` targeting bins below and above price, `vm.warp` to advance time, push oracle to 1005 via the oracle admin, then execute the transaction and assert `amount0Added == 0` and the resulting position is entirely out-of-range. [12](#0-11)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L100-104)
```text
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(weightDeltas);
    _validatePositiveWeights(weightDeltas);
    _validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L152-179)
```text
  function metricOmmModifyLiquidityCallback(uint256 amount0Delta, uint256 amount1Delta, bytes calldata callbackData)
    external
    override
  {
    uint8 kind = abi.decode(callbackData, (uint8));
    if (kind == KIND_PROBE) {
      revert LiquidityProbe(amount0Delta, amount1Delta);
    }
    if (kind != KIND_PAY) revert InvalidCallbackKind();

    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
    _clearPayContext();
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L263-286)
```text
  function _validateBinAndBinPosition(
    address pool,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition
  ) internal view {
    if (minimalCurBin > maximalCurBin) {
      revert CursorOutOfBounds(0, 0, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }

    (, int8 curBinIdx, uint104 curPosInBin,,,) = PoolStateLibrary._slot0(pool);

    int256 curBin = curBinIdx;
    if (curBin < minimalCurBin || curBin > maximalCurBin) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
    if (curBinIdx == minimalCurBin && curPosInBin < minimalPosition) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
    if (curBinIdx == maximalCurBin && curPosInBin > maximalPosition) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
  }
```
