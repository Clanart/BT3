The vulnerability is real. Let me trace it precisely.

## Core Issue: `pay()` Hybrid WETH Path Consumes Stale Router ETH

The `pay` function in `PeripheryPayments.sol` has three branches for `token == WETH`: [1](#0-0) 

When `nativeBalance > 0` and `nativeBalance < value`, the router wraps its own ETH balance and sends it to the recipient, then pulls only `value - nativeBalance` from the payer. This is the hybrid path that consumes any ETH sitting in the router, regardless of who deposited it.

## How ETH Accumulates in the Router

The `receive()` guard blocks direct ETH transfers: [2](#0-1) 

However, `payable` function calls bypass `receive()` entirely — ETH sent as `msg.value` to `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, or `multicall` goes directly to the contract. If a user sends more ETH than `amountIn` (e.g., 2 ETH for a 1 WETH swap), the router wraps exactly `amountIn` in the callback and leaves the remainder. Without a `refundETH()` call in the same multicall, that ETH is stranded. [3](#0-2) 

## Attack Path

1. **User A** calls `exactInputSingle{value: 2 ETH}(amountIn=1 ETH, tokenIn=WETH, ...)`. The callback wraps 1 ETH and pays the pool. 1 ETH remains in the router. User A does not call `refundETH()`.
2. **User B** calls `exactInputSingle(amountIn=1.5 WETH, tokenIn=WETH, ...)` with no ETH attached. In the callback, `pay(WETH, UserB, pool, 1.5e18)` is called. `nativeBalance = 1 ETH > 0` and `1 ETH < 1.5 ETH`, so the hybrid branch fires: router wraps 1 ETH → sends to pool, then pulls only 0.5 WETH from User B.
3. **Result**: User A loses 1 ETH. User B pays 1 WETH less than owed. The pool receives the correct 1.5 WETH total, so no pool insolvency — but User A's principal is stolen.

The callback path confirms `payer = msg.sender` (the original caller) is set at entry: [4](#0-3) 

And `_justPayCallback` passes that payer directly to `pay()`: [5](#0-4) 

There is no guard in `pay()` that checks whether the router's ETH balance belongs to the current transaction's sender.

---

### Title
Stale Router ETH Consumed by Subsequent WETH Swap via Hybrid `pay()` Path, Causing Direct Loss of Prior Depositor's ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
The `pay()` hybrid WETH branch unconditionally consumes the router's entire native ETH balance to partially fund any WETH payment, without verifying that the ETH belongs to the current payer. Residual ETH from a prior over-funded WETH swap is silently stolen by the next WETH swap caller.

### Finding Description
`PeripheryPayments.pay()` checks `address(this).balance` globally. When `0 < nativeBalance < value` and `token == WETH`, it wraps and forwards the router's full ETH balance, then pulls only the shortfall from the payer. Any ETH left in the router from a previous transaction (e.g., a user who sent excess `msg.value` and omitted `refundETH()`) is consumed to subsidize the new payer's obligation.

### Impact Explanation
Direct loss of ETH principal for any user who leaves ETH in the router without immediately reclaiming it. The loss is silent — the victim's transaction already succeeded, and the theft occurs in a later, unrelated transaction. Severity: **High**.

### Likelihood Explanation
Realistic. Users commonly send a round ETH amount for WETH swaps and rely on `refundETH()` in a multicall. A dropped or failed `refundETH()` call, a simple EOA call without multicall, or any revert in a later multicall step leaves ETH stranded. The next WETH swap by any user triggers the theft automatically.

### Recommendation
In the hybrid WETH branch, do not consume the router's ETH balance for a third-party payer. Either:
- Remove the hybrid path entirely and require callers to pre-wrap ETH themselves, or
- Only use `address(this).balance` when `payer == address(this)` (i.e., mid-path hops), or
- Track per-transaction ETH deposits (e.g., via `msg.value` stored in transient storage at entry) and limit the hybrid path to that amount only.

### Proof of Concept
```solidity
// 1. User A over-funds a WETH swap, leaving 1 ETH in router
router.exactInputSingle{value: 2 ether}(
    ExactInputSingleParams({tokenIn: WETH, amountIn: 1 ether, ...})
);
// User A forgets refundETH(); router now holds 1 ETH

// 2. User B swaps 1.5 WETH — hybrid path fires
// Router wraps its 1 ETH, pulls only 0.5 WETH from User B
router.exactInputSingle(
    ExactInputSingleParams({tokenIn: WETH, amountIn: 1.5 ether, ...})
);

// Assert: router ETH balance == 0 (User A's 1 ETH consumed)
// Assert: User B's WETH balance reduced by only 0.5 WETH, not 1.5 WETH
// Assert: pool received correct 1.5 WETH (no revert, theft is silent)
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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
