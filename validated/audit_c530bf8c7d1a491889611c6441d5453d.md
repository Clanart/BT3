Audit Report

## Title
Stranded ETH from overpaid swaps stolen via unrestricted `refundETH()` or silently consumed by subsequent `pay()` calls — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`refundETH()` transfers `address(this).balance` to any `msg.sender` with no check that the caller deposited that ETH. Separately, `pay()` reads `address(this).balance` — the entire contract balance — when wrapping ETH for WETH swaps. When a user overpays an exact-output swap and omits `refundETH()` from their multicall (or calls the swap directly), the surplus ETH is permanently stranded in the router and claimable by any subsequent caller.

## Finding Description
`refundETH()` is defined as: [1](#0-0) 

It transfers the entire `address(this).balance` to `msg.sender` with no accounting of who deposited which ETH. Any caller can invoke it at any time.

`pay()` when `token == WETH` reads: [2](#0-1) 

It uses `address(this).balance` — the whole contract balance, not just the current transaction's `msg.value` — to wrap ETH before pulling from the payer. Stranded ETH from a prior transaction is consumed first.

ETH becomes stranded via `exactOutputSingle`, which is `payable` and accepts up to `amountInMaximum` ETH upfront: [3](#0-2) 

The swap callback `_justPayCallback` calls `pay()` with the actual `amountIn` determined by the pool: [4](#0-3) 

`pay()` wraps only the required ETH; the surplus remains in the router. `exactOutputSingle` does not auto-refund after the swap — it only checks `amountIn > amountInMaximum` and clears transient storage. The `receive()` guard: [5](#0-4) 

only blocks unsolicited direct ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` on payable entry points. The same stranding risk applies to `exactOutput`, `exactInputSingle`, and `exactInput`.

## Impact Explanation
Direct loss of user principal. A user who calls `exactOutputSingle{value: 2 ETH}` where the actual `amountIn` is 1 ETH, without appending `refundETH()` in a multicall, permanently loses 1 ETH. The loss is proportional to the overpayment. No privileged role is required; any unprivileged address can call `refundETH()` at any time to drain the full stranded balance. This meets the Sherlock threshold for Medium-to-High direct loss of user principal.

## Likelihood Explanation
Medium. The Uniswap v3 multicall pattern requires users to explicitly append `refundETH()` to recover unused ETH — a well-known footgun. Users who call `exactOutputSingle` or `exactOutput` directly (without multicall), or who construct a multicall without the refund step, will strand ETH. Frontrunners monitoring the mempool can observe a stranded-ETH state after any such transaction and immediately call `refundETH()` to drain it. The `pay()` consumption path requires no active attacker — it fires automatically on the next WETH swap.

## Recommendation
1. **Track per-transaction ETH via transient storage**: Store `msg.value` at each payable entry point and use only that tracked amount in `pay()` instead of `address(this).balance`. Clear the tracked value at the end of each top-level call.
2. **Restrict `refundETH()` to the depositor**: Record the depositor address in transient storage at the start of each payable entry point and enforce it in `refundETH()`.
3. **Auto-refund surplus**: After each swap, compute `address(this).balance` minus any expected remainder and push it back to `msg.sender` automatically, eliminating the need for a separate `refundETH()` call.

## Proof of Concept
```
1. Alice calls exactOutputSingle{value: 2 ether}(
       tokenIn=WETH, amountOut=X, amountInMaximum=2 ether, ...
   )
   - Pool callback fires; _justPayCallback calls pay(WETH, Alice, pool, 1 ETH)
   - pay() sees address(this).balance = 2 ETH >= value = 1 ETH
   - pay() wraps 1 ETH → sends WETH to pool
   - 1 ETH remains in router
   - Alice does NOT call refundETH() (called directly, not via multicall)

2. Bob calls refundETH() in a standalone tx
   - balance = address(this).balance = 1 ETH
   - _transferETH(Bob, 1 ETH) executes
   - Bob receives Alice's 1 ETH

   OR

2'. Bob calls exactInputSingle{value: 0}(tokenIn=WETH, amountIn=0.5 ETH, ...)
   - pay() sees address(this).balance = 1 ETH >= 0.5 ETH
   - pay() wraps 0.5 ETH from Alice's stuck balance → Bob's swap fully funded
   - Bob pays 0 ETH from his own balance; Alice loses 0.5 ETH
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-199)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
  }
```
