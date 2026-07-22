### Title
Unverified Native ETH Balance in `pay()` Allows Any Caller to Drain Stranded ETH — (`File: metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay()` uses `address(this).balance` as the payment source for WETH-leg swaps without verifying that the native ETH was sent by the current `payer`. Any ETH left in the router from a prior transaction (e.g., a user who called a payable swap function directly without appending `refundETH()`) is silently consumed by the next caller who performs a WETH swap, resulting in direct loss of the original sender's ETH.

### Finding Description
`PeripheryPayments.pay()` contains the following logic for WETH-leg payments:

```solidity
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
}
``` [1](#0-0) 

The function reads `address(this).balance` — the router's total native ETH balance — and uses it to pay the pool on behalf of `payer`. There is no check that this ETH was deposited by `payer` in the current transaction. Any ETH stranded from a previous transaction is indistinguishable from ETH sent by the current caller.

All four swap entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) are `external payable`, so a user can call them directly with `{value: X}` without wrapping the call in a `multicall` that includes `refundETH()`. [2](#0-1) 

When `amountIn < msg.value`, the swap callback consumes only `amountIn` wei; the remainder stays in the router with no automatic refund mechanism. [3](#0-2) 

`refundETH()` sends the entire balance to `msg.sender`, but it is only called if the user explicitly includes it. There is no guard preventing a subsequent caller from benefiting from the stranded balance first.

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks plain ETH transfers; it does not prevent ETH from entering via `msg.value` on any payable function call. [4](#0-3) 

The same `pay()` function is shared by `MetricOmmPoolLiquidityAdder`, so the identical vulnerability applies to the liquidity-adder path. [5](#0-4) 

### Impact Explanation
A user who calls any payable swap or liquidity function directly with `msg.value > amountActuallyNeeded` and omits `refundETH()` leaves the surplus ETH in the router. The next caller who performs a WETH-input swap (with zero or insufficient `msg.value`) has their payment silently satisfied from the stranded ETH. The victim loses the stranded amount; the attacker receives the swap output without paying.

This is a direct loss of user principal with no recovery path once the attacker's transaction executes.

### Likelihood Explanation
- All payable swap functions are callable directly without `multicall`, making it easy for users to accidentally strand ETH.
- An attacker can monitor the mempool or the router's ETH balance and front-run or follow up with a zero-value WETH swap to drain the stranded amount.
- No special privileges are required; any unprivileged address can trigger the theft.

### Recommendation
Track the ETH contributed by the current call in transient storage (e.g., record `msg.value` at entry and decrement it as it is consumed in `pay()`). Only use the tracked amount as native ETH in `pay()`; revert or fall back to `transferFrom` if the tracked balance is insufficient. Alternatively, enforce that any payable swap entry point either consumes all `msg.value` or automatically refunds the remainder before returning.

### Proof of Concept

```
// Step 1 – Alice strands ETH
// Alice calls exactInputSingle directly with 1 ether but amountIn = 100 wei
// She does NOT include refundETH() in a multicall.
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    pool: wethToken1Pool,
    tokenIn: WETH,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 100,          // only 100 wei consumed
    amountOutMinimum: 0,
    recipient: alice,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// router.balance == 1 ether - 100 wei  (stranded)

// Step 2 – Bob steals Alice's ETH
// Bob sends 0 ETH but requests a 500-wei WETH swap.
// pay(WETH, Bob, pool, 500) sees nativeBalance = 1 ether - 100 >= 500,
// wraps 500 wei of Alice's ETH, and sends it to the pool.
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    pool: wethToken1Pool,
    tokenIn: WETH,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 500,          // paid from Alice's stranded ETH
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Bob receives token1 output; Alice's 500 wei is gone.
``` [6](#0-5)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```
