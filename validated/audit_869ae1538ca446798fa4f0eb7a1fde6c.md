The code confirms this is a real vulnerability. Let me trace the exact path:

**`exactOutputSingle`** sets `payer = msg.sender` (User B) and `tokenIn = WETH`: [1](#0-0) 

The pool callback fires `_justPayCallback`, which calls `pay(WETH, payer=UserB, pool, amountIn)`: [2](#0-1) 

Inside `pay`, the WETH branch checks the **router's total native balance** — not `msg.value` of the current call: [3](#0-2) 

If `address(this).balance >= value`, it wraps that ETH and transfers it to the pool — **no `transferFrom` on User B occurs at all**. Any ETH stranded from a prior user's overpayment (e.g., User A sent excess `msg.value` and didn't call `refundETH()`) is silently consumed to settle User B's swap.

---

### Title
Cross-User ETH Theft via Residual Router Balance in WETH Payment Path — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay` uses `address(this).balance` (the router's total ETH) to settle WETH-denominated swap payments, without bounding consumption to the current caller's `msg.value`. A caller with zero `msg.value` can drain ETH left by a prior user.

### Finding Description
When `exactOutputSingle` (or `exactInput*`/`exactOutput*`) is called with `tokenIn = WETH`, the swap callback invokes `pay(WETH, payer=msg.sender, pool, amountIn)`.

In `PeripheryPayments.pay`:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // router's TOTAL ETH
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value); // no pull from payer
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

ETH accumulates in the router whenever any user calls a payable function with excess `msg.value` and does not follow up with `refundETH()`. This is a normal and expected pattern (e.g., `exactOutputSingle` with a conservative `amountInMaximum` in ETH). The router is `payable` and designed to hold ETH transiently.

Attack path:
1. User A calls `exactOutputSingle` with `tokenIn=WETH`, sends `msg.value = X` ETH (X > actual `amountIn`), and does not call `refundETH()`. Residual `X - amountIn_A` ETH remains in the router.
2. User B calls `exactOutputSingle` with `tokenIn=WETH`, `msg.value=0`, `amountInMaximum=type(uint256).max`.
3. `pay(WETH, UserB, pool, amountIn_B)` fires. `nativeBalance = X - amountIn_A`. If this covers `amountIn_B`, the router wraps User A's ETH and sends it to the pool. User B is never pulled from.
4. User B receives swap output at zero cost. User A's ETH is permanently consumed.

### Impact Explanation
Direct loss of User A's ETH principal. User B receives a fully-settled swap without contributing any funds. The loss is bounded only by the residual ETH in the router at the time of the attack, which can be up to the full `msg.value` of a prior user's transaction. Severity: **High**.

### Likelihood Explanation
ETH stranding is a routine occurrence: any user who calls `exactOutputSingle` with `tokenIn=WETH` and `msg.value > amountIn` without bundling `refundETH()` in a `multicall` leaves residual ETH. The attack requires no special permissions, no malicious pool, and no non-standard tokens — only a public call to `exactOutputSingle`.

### Recommendation
Track the ETH available to the **current call** rather than the router's total balance. The standard Uniswap V3 approach is to pass `msg.value` down through the call stack and consume only from it:

```solidity
// In pay(), replace address(this).balance with a per-call ETH budget
// passed as a parameter or stored in transient storage at entry.
if (nativeBalance >= value) { ... }
```

Alternatively, enforce that `address(this).balance` at the start of each top-level entry equals `msg.value` (i.e., revert if the router holds residual ETH), though this is more restrictive.

### Proof of Concept
```solidity
// Foundry test sketch
function test_crossUserETHTheft() public {
    // User A: swap with excess ETH, no refundETH
    uint256 excessETH = 1 ether;
    router.exactOutputSingle{value: excessETH}(ExactOutputSingleParams({
        pool: pool, tokenIn: WETH, tokenOut: tokenB,
        amountOut: smallAmount, amountInMaximum: excessETH,
        recipient: userA, zeroForOne: true, ...
    }));
    // Router now holds (excessETH - actualAmountIn) residual ETH

    uint256 routerBalanceBefore = address(router).balance; // > 0

    // User B: zero msg.value, steals router's ETH
    uint256 amountInPaid = router.exactOutputSingle{value: 0}(ExactOutputSingleParams({
        pool: pool, tokenIn: WETH, tokenOut: tokenB,
        amountOut: smallAmount, amountInMaximum: type(uint256).max,
        recipient: userB, zeroForOne: true, ...
    }));

    // User B received output; router's ETH was consumed; User B paid nothing
    assertEq(address(router).balance, routerBalanceBefore - amountInPaid);
    assertEq(IERC20(tokenB).balanceOf(userB), smallAmount);
}
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-135)
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
