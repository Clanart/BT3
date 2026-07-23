Audit Report

## Title
Stranded ETH on the router is silently consumed by any WETH-input swap, enabling theft of prior users' funds - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
`PeripheryPayments.pay()` unconditionally uses the router's entire native ETH balance to settle WETH-input swaps when `address(this).balance >= value`, completely bypassing the designated `payer`. ETH left on the router by any prior user — e.g., excess ETH from a `multicall{value}` call that omits `refundETH()` — is silently consumed by the next caller who initiates a WETH-input swap, giving that caller free output tokens at the prior user's expense.

## Finding Description
In `pay()`, the WETH branch reads the full contract balance and, if it covers the owed amount, wraps and forwards that ETH to the pool without ever touching `payer`: [1](#0-0) 

When `nativeBalance >= value`, `payer` (set to `msg.sender` of the swap initiator via `_setNextCallbackContext`) is completely ignored. The router's ETH balance is an unattributed shared pool.

The callback path that reaches `pay()` is:

- `exactInputSingle` sets callback context with `msg.sender` as payer and `params.tokenIn` as token: [2](#0-1) 
- The pool calls back into `metricOmmSwapCallback` → `_justPayCallback` → `pay(_getTokenToPay(), _getPayer(), msg.sender, amount)`: [3](#0-2) 

ETH accumulates on the router because `multicall` has no automatic refund: [4](#0-3) 

The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via payable function calls: [5](#0-4) 

## Impact Explanation
Direct, irrecoverable loss of user principal: ETH stranded on the router by User A is consumed to pay for User B's swap. User A loses their ETH; User B receives output tokens without contributing any funds. The pool receives its owed WETH input, but the designated payer contributes nothing — the invariant "payer pays" is broken. This constitutes a swap conservation failure and direct loss of user principal, qualifying as High severity under the allowed impact gate.

## Likelihood Explanation
ETH is stranded on the router whenever a user sends ETH with `multicall{value: X}` and omits `refundETH()` — a common pattern when users send excess ETH to cover slippage. An attacker can monitor the router's ETH balance on-chain and exploit it in the same block. No special permissions, approvals, or privileged roles are required. The attack is repeatable and unconditional.

## Recommendation
Track the ETH contributed by the **current call** separately from any pre-existing balance. One approach: record `msg.value` at `multicall` entry and deduct from it as ETH is consumed in `pay()`, reverting if the current call's ETH budget is exhausted. Alternatively, require WETH-input swaps to always pull from the payer's WETH allowance and never use the router's native balance unless `payer == address(this)`.

## Proof of Concept
1. User A calls `router.multicall{value: 1 ether}([exactInputSingle(tokenIn=WETH, amountIn=0.5 ether, ...)])`. The swap consumes 0.5 ETH via `pay()`; 0.5 ETH remains on the router. User A omits `refundETH()`.
2. Attacker calls `router.exactInputSingle(tokenIn=WETH, amountIn=0.5 ether, ...)` with no ETH attached.
3. Inside `pay()`, `nativeBalance = 0.5 ether >= value = 0.5 ether`, so the router wraps and forwards the stranded ETH to the pool. `safeTransferFrom` is never called on the attacker.
4. Attacker receives output tokens; User A's 0.5 ETH is permanently lost with no on-chain remedy.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
