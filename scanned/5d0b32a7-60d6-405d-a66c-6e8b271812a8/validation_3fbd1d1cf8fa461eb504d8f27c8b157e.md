### Title
Stranded ETH on the router is silently consumed by any WETH-input swap, enabling theft of prior users' funds - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

### Summary
`PeripheryPayments.pay()` uses the router's **entire** native ETH balance to settle WETH-input swaps before it ever touches the designated `payer`. Any ETH left on the router by a prior user (e.g., excess ETH from a `multicall{value}` call that omits `refundETH()`) is silently consumed by the next caller who swaps with `tokenIn = WETH`, giving that caller free output tokens at the prior user's expense.

### Finding Description
In `PeripheryPayments.pay()`, the WETH branch reads the router's full native balance and, if it covers the owed amount, wraps and forwards that ETH to the pool without pulling anything from `payer`:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // entire contract balance
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value); // payer is never touched
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
``` [1](#0-0) 

The `payer` argument (set to `msg.sender` of the swap initiator by `_setNextCallbackContext`) is completely ignored when `nativeBalance >= value`. The router's ETH balance is an unattributed shared pool: any ETH sent with a payable call that is not explicitly refunded via `refundETH()` persists on the contract and is available to any subsequent WETH-input swap.

The callback path that reaches `pay()` is:

```
exactInputSingle / exactInput
  → _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, msg.sender, tokenIn)
  → pool.swap(...)
    → metricOmmSwapCallback(...)
      → _justPayCallback(...)
        → pay(_getTokenToPay(), _getPayer(), msg.sender, amount)
``` [2](#0-1) [3](#0-2) 

`_getPayer()` returns the attacker's address, but `pay()` never calls `safeTransferFrom` on it when the router holds enough ETH.

The `multicall` function has no automatic ETH refund; users must explicitly include `refundETH()` in their batch: [4](#0-3) 

The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `payable` function calls: [5](#0-4) 

### Impact Explanation
- **Direct loss of user principal**: ETH stranded on the router by User A is consumed to pay for User B's swap. User A loses their ETH; User B receives output tokens for free.
- **Swap conservation failure**: The pool receives its owed WETH input, but the designated payer (the attacker) contributes nothing. The invariant "payer pays" is broken.
- Severity: **High** — unconditional, irrecoverable loss of stranded ETH with no on-chain remedy for the victim.

### Likelihood Explanation
ETH is stranded on the router whenever a user sends ETH with `multicall{value: X}` and omits `refundETH()` from the batch — a common pattern when users send excess ETH to cover slippage. An attacker can monitor the router's ETH balance on-chain and exploit it in the same block. No special permissions or approvals are required.

### Recommendation
Track the ETH contributed by the **current call** separately from any pre-existing balance. One approach: record `msg.value` at `multicall` entry and deduct from it as ETH is consumed in `pay()`, reverting if the current call's ETH budget is exhausted. Alternatively, require WETH-input swaps to always pull from the payer's WETH allowance and never use the router's native balance unless `payer == address(this)`.

### Proof of Concept
1. User A calls `router.multicall{value: 1 ether}([exactInputSingle(tokenIn=WETH, amountIn=0.5 ether, ...)])`. The swap uses 0.5 ETH; 0.5 ETH remains on the router. User A omits `refundETH()`.
2. Attacker calls `router.exactInputSingle(tokenIn=WETH, amountIn=0.5 ether,

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-87)
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
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-85)
```text
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
