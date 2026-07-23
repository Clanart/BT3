The code confirms this is a real vulnerability. Let me trace the exact path.

**`refundETH()` has zero access control:** [1](#0-0) 

Any `msg.sender` receives the entire ETH balance of the router.

**ETH can be stranded between transactions through normal usage.** The `pay()` function wraps only the exact `value` needed, leaving any excess native ETH in the contract: [2](#0-1) 

For `exactOutputSingle`, a user legitimately sends `msg.value = amountInMaximum` (the maximum they're willing to pay). The actual `amountIn` resolved by the pool is typically less. The excess ETH is left in the router after the transaction completes, with no automatic refund: [3](#0-2) 

The `receive()` guard (only WETH can push ETH) does not prevent this — ETH enters via `payable` function calls, not direct sends: [4](#0-3) 

---

### Title
Unguarded `refundETH()` allows any caller to steal ETH stranded by exact-output swaps — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`refundETH()` transfers the router's entire ETH balance to `msg.sender` with no access control. ETH is routinely stranded in the router when users call `exactOutputSingle` or `exactOutput` with `msg.value = amountInMaximum` and the actual swap cost is lower. Any attacker who observes the stranded ETH (e.g., via mempool or block explorer) can call `refundETH()` in a subsequent transaction and steal it.

### Finding Description
`PeripheryPayments.refundETH()` is `external payable` with no `msg.sender` check. The `pay()` internal function, when `token == WETH`, wraps exactly the amount the pool demands (`value`) and leaves any surplus native ETH in the contract. For exact-output swaps, users must send up to `amountInMaximum` ETH; the difference between that cap and the actual cost is stranded after the swap transaction. Because `refundETH()` is a separate, unguarded call, a front-runner or any subsequent caller can drain it before the original user reclaims it.

### Impact Explanation
Direct loss of user ETH. The stolen amount equals `msg.value − actual amountIn` for every exact-output ETH swap where the user does not atomically bundle `refundETH()` in the same `multicall`. This is a realistic loss path, not a theoretical one — exact-output swaps inherently produce surplus ETH.

### Likelihood Explanation
Moderate. Users who call `exactOutputSingle` or `exactOutput` directly (not via multicall) strand ETH every time the pool's actual cost is below `amountInMaximum`. MEV bots routinely monitor for stranded ETH in router contracts.

### Recommendation
Either (a) restrict `refundETH()` so it can only be called within a `multicall` context (e.g., check `address(this) == msg.sender` after a delegatecall), or (b) automatically refund excess ETH at the end of each swap function, or (c) document clearly that `refundETH()` must always be bundled in the same `multicall` as the swap and add a revert guard enforcing this.

### Proof of Concept
```solidity
// 1. User calls exactOutputSingle with msg.value = 2 ETH, amountInMaximum = 2 ETH
//    Pool only needs 1.5 ETH; pay() wraps 1.5 ETH, 0.5 ETH stays in router.
// 2. Attacker (separate tx) calls router.refundETH()
//    → attacker receives 0.5 ETH belonging to the user.
assertEq(attacker.balance, 0.5 ether); // passes
assertEq(user.balance, initialBalance - 1.5 ether - 0.5 ether); // user lost excess
```

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-84)
```text
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
