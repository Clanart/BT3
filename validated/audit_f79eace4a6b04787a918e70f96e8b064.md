### Title
Excess native ETH sent to `exactOutputSingle` / `exactOutput` is not refunded and can be stolen via `refundETH()` - (File: metric-periphery/contracts/base/PeripheryPayments.sol, metric-periphery/contracts/MetricOmmSimpleRouter.sol)

### Summary

`exactOutputSingle` and `exactOutput` are `payable` and accept native ETH as a WETH substitute via `pay()`. When the actual `amountIn` determined by the pool is less than `msg.value`, the surplus ETH is silently left in the router. Because `refundETH()` sends the entire router ETH balance to `msg.sender` with no access control, any third party can call it in a subsequent transaction and steal the stranded ETH.

### Finding Description

`pay()` in `PeripheryPayments` handles native ETH by wrapping exactly the amount the pool requests:

```solidity
// PeripheryPayments.sol L73-77
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();   // wraps only `value`, not nativeBalance
        IERC20(WETH).safeTransfer(recipient, value);
``` [1](#0-0) 

When `nativeBalance > value`, the difference `nativeBalance - value` remains as raw ETH in the router. Neither `exactOutputSingle` nor `exactOutput` contain any post-swap refund logic: [2](#0-1) 

`refundETH()` is a public, permissionless function that sends the entire router ETH balance to `msg.sender`:

```solidity
// PeripheryPayments.sol L58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [3](#0-2) 

There is no check that the caller is the original depositor. Any address that calls `refundETH()` after a swap that left surplus ETH receives all of it.

The same vulnerability applies to `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` and `addLiquidityWeighted`, which are also `payable` and inherit the same `pay()` / `refundETH()` pair. [4](#0-3) 

### Impact Explanation

A user who sends `msg.value > actualAmountIn` (e.g., they quoted 1.2 ETH but the pool only consumed 1 ETH due to favorable price movement) loses the surplus ETH permanently if an attacker front-runs or back-runs with a `refundETH()` call. The loss is direct user principal, not dust — it scales with the size of the overpayment.

### Likelihood Explanation

Two realistic triggers exist:

1. **Price movement between quote and execution**: A user quotes `amountIn` off-chain, sends that exact value as `msg.value`, but the pool price moves favorably so the actual `amountIn` is smaller. The surplus is stranded.
2. **Deliberate buffer**: A user intentionally sends slightly more ETH than the quote to avoid slippage reverts (analogous to the external report's "incorrect fee calculation" scenario).

Neither requires a malicious user setup. The attacker only needs to monitor the mempool for transactions that leave ETH in the router and call `refundETH()` in the same block.

### Recommendation

Either:
- **Strict**: After the swap callback settles, compute `address(this).balance` and revert if it is non-zero (forcing users to send exact ETH via multicall).
- **Refund**: Automatically call `_transferETH(msg.sender, address(this).balance)` at the end of `exactOutputSingle` and `exactOutput` when `tokenIn == WETH` and `address(this).balance > 0`.
- **Document + enforce multicall**: Require callers to use `multicall([exactOutputSingle(...), refundETH()])` and add a `nonPayable` guard to the single-call entry points (breaking change, but eliminates the attack surface).

### Proof of Concept

```
1. Pool price: 1 ETH in → 1500 token1 out (quoted off-chain)
2. User calls:
       router.exactOutputSingle{value: 1.2 ether}(ExactOutputSingleParams{
           tokenIn: WETH, amountOut: 1500, amountInMaximum: 2 ether, ...
       })
3. Price moved; pool only needs 0.9 ETH. pay() wraps 0.9 ETH, sends to pool.
   → 0.3 ETH remains in router.
4. Attacker (watching mempool) calls router.refundETH() in the same block.
   → Attacker receives 0.3 ETH. User's 0.3 ETH is permanently lost.
``` [5](#0-4) [2](#0-1)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
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
