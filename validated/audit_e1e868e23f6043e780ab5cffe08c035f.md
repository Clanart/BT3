### Title
Excess ETH sent to payable swap and liquidity functions is not automatically refunded and can be stolen by any caller — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

### Summary
The `exactOutputSingle`, `exactOutput`, `addLiquidityExactShares`, and `addLiquidityWeighted` functions are `payable` and accept native ETH for WETH-denominated swaps and liquidity additions. The internal `pay()` function wraps only the exact required amount of ETH into WETH, leaving any excess ETH sitting in the contract. Because `refundETH()` is permissionless and sends the entire contract ETH balance to `msg.sender`, any third party can call it in a subsequent transaction to steal the excess ETH that the original user left behind.

### Finding Description
`PeripheryPayments.pay()` handles WETH-leg payments by inspecting `address(this).balance`:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
}
```

Only `value` wei is consumed; any surplus remains in the contract. The refund helper is:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // sends to caller, not original payer
    }
}
```

`refundETH()` is unrestricted: it sends the full contract balance to whoever calls it. There is no binding between the original ETH sender and the refund recipient. If a user calls `exactOutputSingle` or `exactOutput` directly (not via `multicall`) with more ETH than the pool ultimately charges, or simply omits the `refundETH()` leg from their multicall, the surplus is claimable by any address in a later transaction.

The exact-output family is the highest-risk surface: the caller supplies `amountInMaximum` but the pool charges only the actual market-determined `amountIn ≤ amountInMaximum`. The gap `amountInMaximum − amountIn` is left in the contract with no automatic return path.

### Impact Explanation
Direct loss of user principal. A user who sends 1 ETH for an exact-output swap that costs 0.8 ETH loses 0.2 ETH to any address that races to call `refundETH()` before the user can. The loss is proportional to the over-send and is bounded only by `amountInMaximum − actual amountIn`. For liquidity additions via `addLiquidityWeighted`, the probe-then-scale flow makes the final ETH consumption unpredictable at call time, widening the gap further.

### Likelihood Explanation
Medium. The Uniswap v3-style multicall-plus-`refundETH` pattern is non-obvious to integrators and end users. Any direct call to a payable swap or liquidity function with a conservative (over-estimated) ETH value — a common defensive pattern — silently leaves funds at risk. MEV bots routinely monitor for stranded ETH in known router contracts.

### Recommendation
Add an automatic ETH refund at the end of each payable entry point, mirroring the external report's suggested fix:

```solidity
function exactOutputSingle(ExactOutputSingleParams calldata params)
    external payable returns (uint256 amountIn)
{
    // ... existing logic ...
    _clearExpectedCallbackPool();

    // Refund unused ETH to caller
    uint256 leftover = address(this).balance;
    if (leftover > 0) _transferETH(msg.sender, leftover);
}
```

Apply the same tail-refund to `exactOutput`, `addLiquidityExactShares`, and `addLiquidityWeighted`. Alternatively, for exact-input single-hop swaps where `amountIn` is caller-specified, enforce `msg.value == amountIn` when `tokenIn == WETH` to eliminate the surplus entirely.

### Proof of Concept
1. Pool is priced such that swapping to receive 1 000 token1 costs 800 wei of WETH.
2. User calls `router.exactOutputSingle{value: 1000}(params)` where `params.amountInMaximum = 1000` and `params.tokenIn = WETH`.
3. `pay()` wraps 800 wei and transfers it to the pool; 200 wei remains in the router.
4. User's transaction ends without a `refundETH()` call (direct invocation, not multicall).
5. Attacker calls `router.refundETH()` in the next block; receives 200 wei.
6. User's net ETH loss: 200 wei above the swap cost, with no recourse. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L57-63)
```text
  /// @inheritdoc IPeripheryPayments
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-88)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
      } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
      }
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-188)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint8 tradesLeftAfterThis = uint8(params.pools.length - 1);
    address pool = params.pools[tradesLeftAfterThis];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, tradesLeftAfterThis);
    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
    );
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );

    int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = _getExactOutputAmountIn();
    _clearExpectedCallbackPool();
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
