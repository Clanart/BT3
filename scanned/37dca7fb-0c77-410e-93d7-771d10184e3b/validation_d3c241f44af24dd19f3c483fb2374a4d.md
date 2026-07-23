The vulnerability is real. Let me trace the exact execution path through the code.

---

## Execution Trace

**Step 1 — ETH stranding (User A)**

User A calls `exactInputSingle` (or `exactOutputSingle`) with `tokenIn=WETH` and `msg.value > amountIn`, then never calls `refundETH()`. The excess ETH remains in the router across transaction boundaries.

**Step 2 — Exploit (User B)**

User B calls `exactOutputSingle` with `tokenIn=WETH`, `msg.value=0`, `amountInMaximum=MAX_UINT`. [1](#0-0) 

`_setNextCallbackContext` stores `payer = msg.sender` (User B) and `tokenToPay = WETH` in transient storage.

**Step 3 — Callback fires**

The pool calls `metricOmmSwapCallback`, which dispatches to `_justPayCallback`: [2](#0-1) 

This calls `pay(WETH, UserB, pool, amountIn)`.

**Step 4 — The vulnerable branch in `pay()`** [3](#0-2) 

`nativeBalance = address(this).balance` reads the **router's total ETH balance**, which includes User A's stranded ETH — not just `msg.value` from User B's call (which is 0). If `nativeBalance >= amountIn`, the router wraps User A's ETH and transfers it to the pool as WETH, settling User B's swap entirely from User A's funds.

---

### Title
Cross-User ETH Theft via Residual Router Balance in `pay()` WETH Branch — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay()` uses `address(this).balance` (the router's total native ETH) rather than the current call's `msg.value` to fund WETH payments. Any ETH stranded in the router from a prior user's transaction can be consumed by a subsequent caller's WETH swap.

### Finding Description
When `pay()` is invoked with `token == WETH` and `payer != address(this)`, it checks `address(this).balance` at line 74:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
``` [4](#0-3) 

This balance is a **contract-level accumulator**, not scoped to the current transaction's `msg.value`. ETH strands in the router whenever a user sends `msg.value > amountIn` and omits a `refundETH()` call. The `receive()` guard only blocks non-WETH ETH pushes; it does not prevent accumulation from overpayment. [5](#0-4) 

A subsequent caller with `msg.value=0` and `tokenIn=WETH` will have their swap settled using the prior user's ETH. The `amountIn > params.amountInMaximum` guard in `exactOutputSingle` only caps the amount paid — it does not verify *who* paid it. [6](#0-5) 

### Impact Explanation
User A loses ETH they could have recovered via `refundETH()`. User B receives a swap output without spending any ETH or WETH. This is direct, cross-user theft of principal. Impact: **High**.

### Likelihood Explanation
ETH stranding is a natural consequence of any WETH-path swap where `msg.value` exceeds the actual swap cost (e.g., slippage buffers, partial fills, or user error). The attacker only needs to observe a non-zero router ETH balance (on-chain readable) and call `exactOutputSingle` with `tokenIn=WETH` and `msg.value=0`. No privileged access, malicious pool, or non-standard token is required. Likelihood: **Medium** (requires stranded ETH, but the condition arises organically).

### Recommendation
Track the ETH available for the current call in transient storage at the `exactInput*`/`exactOutput*` entry points (storing `msg.value`), and use that tracked value — not `address(this).balance` — inside `pay()`. Alternatively, enforce that `address(this).balance` at the end of each swap entry equals `address(this).balance` at entry minus `msg.value` (i.e., assert no residual ETH is consumed beyond what was sent in the current call).

### Proof of Concept

```solidity
// Foundry test sketch
function test_exactOutputSingle_stealsStrandedETH() public {
    // 1. User A sends 1 ETH excess and does NOT call refundETH()
    vm.deal(userA, 2 ether);
    vm.prank(userA);
    router.exactInputSingle{value: 2 ether}(ExactInputSingleParams({
        tokenIn: WETH, amountIn: 1 ether, /* ... */
    }));
    // router.balance == 1 ether (stranded)

    // 2. User B calls exactOutputSingle with WETH tokenIn, zero msg.value
    vm.deal(userB, 0);
    vm.prank(userB);
    uint256 amountIn = router.exactOutputSingle{value: 0}(ExactOutputSingleParams({
        tokenIn: WETH, amountOut: TARGET, amountInMaximum: type(uint256).max, /* ... */
    }));

    // 3. User B's swap settled; router consumed User A's 1 ETH
    assertEq(address(router).balance, 0);
    // User B spent 0 ETH/WETH from their own balance
    assertEq(IERC20(WETH).balanceOf(userB), 0); // no WETH spent
}
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-135)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L145-146)
```text
    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
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
