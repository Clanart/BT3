### Title
Partial-ETH payment path in `pay()` silently consumes any prior user's stranded ETH to subsidize a subsequent WETH swap — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments.sol` contains a partial-ETH branch that wraps whatever native ETH the router currently holds and uses it as a first-leg payment toward a WETH swap, then pulls only the remainder from the actual payer's allowance. Because the router's ETH balance carries no per-user attribution, any ETH left on the router by a prior transaction (e.g., a user who sent `msg.value` but omitted `refundETH()`) is silently consumed by the very next WETH swap callback, transferring the loss to the original depositor and giving the new swapper an unearned discount.

---

### Finding Description

`pay()` has three branches when `token == WETH` and `payer != address(this)`:

```
nativeBalance >= value  →  wrap all from ETH, pull nothing from payer
nativeBalance > 0       →  wrap nativeBalance from ETH, pull (value − nativeBalance) from payer   ← vulnerable
nativeBalance == 0      →  pull all from payer
```

The middle branch is the root cause. It unconditionally drains the router's entire ETH balance as a partial payment, regardless of which user deposited that ETH. Uniswap v3's analogous `pay()` only uses router ETH when `address(this).balance >= value`; if the balance is insufficient it falls straight to `safeTransferFrom`. Metric OMM's implementation adds the hybrid branch, which is the new attack surface.

The callback chain that reaches `pay()` is:

```
exactInputSingle / exactInput / exactOutputSingle / exactOutput
  → _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, msg.sender, tokenIn)
  → pool.swap(...)
  → metricOmmSwapCallback(...)
  → _justPayCallback(...)
  → pay(tokenToPay, payer, msg.sender /*pool*/, amount)
```

`payer` is the original `msg.sender` stored in transient storage. When `tokenToPay == WETH`, the partial-ETH branch fires if the router holds any ETH at all, consuming it before touching the payer's allowance.

---

### Impact Explanation

**User A** calls `exactInputSingle` (or any `payable` entry point) with `msg.value = V`, swapping only `A < V` worth of WETH. The callback wraps `A` ETH; the remaining `V − A` ETH stays on the router. If User A omits `refundETH()` (a common mistake in manual calls or incomplete multicalls), `V − A` ETH is permanently stranded.

**User B** then calls `exactInputSingle` with `tokenIn = WETH`, `amountIn = X` where `X > V − A`. In the callback, `pay()` sees `nativeBalance = V − A > 0`, wraps it, sends it to the pool, then pulls only `X − (V − A)` WETH from User B's allowance. User B receives the full swap output while paying `V − A` less WETH than they owe. User A's `V − A` ETH is permanently lost.

The pool is made whole (it receives the correct WETH amount), so no pool insolvency occurs, but User A suffers a direct, irreversible loss of principal equal to the stranded ETH amount.

---

### Likelihood Explanation

- `refundETH()` is not enforced by the router; it must be composed manually into a multicall.
- Any `payable` entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`, `unwrapWETH9`, `sweepToken`) can receive `msg.value` and leave residue.
- The exploit requires no privileged access: any address can call `exactInputSingle` with `tokenIn = WETH` to trigger the partial-ETH branch.
- The attacker can monitor the router's ETH balance on-chain and time the call immediately after a victim's transaction.

---

### Recommendation

Replace the partial-ETH branch with the Uniswap v3 pattern: only use router ETH when it covers the full payment; otherwise fall through to `safeTransferFrom`:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else {
        // Do NOT use partial ETH; pull the full amount from the payer.
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

This eliminates the cross-user ETH consumption while preserving the intended "pay with native ETH" convenience for users who send exactly the right `msg.value`.

---

### Proof of Concept

```
State before:
  router.balance = 0
  User A WETH balance = 1000e18
  User B WETH balance = 1000e18, allowance to router = 1000e18

Step 1 – User A strands ETH:
  User A calls exactInputSingle{value: 200e18}(
      pool, tokenIn=WETH, amountIn=100e18, ...
  )
  Callback: pay(WETH, UserA, pool, 100e18)
    nativeBalance=200e18 >= value=100e18 → wraps 100e18 ETH, sends to pool
  After swap: router.balance = 100e18  (User A forgot refundETH)

Step 2 – User B exploits:
  User B calls exactInputSingle(
      pool, tokenIn=WETH, amountIn=150e18, ...
  )
  Callback: pay(WETH, UserB, pool, 150e18)
    nativeBalance=100e18 > 0, < 150e18 → partial branch fires:
      wraps 100e18 ETH → sends to pool
      safeTransferFrom(UserB, pool, 50e18)   ← only 50e18 pulled from User B

Result:
  User A lost 100e18 ETH (stranded, now consumed)
  User B paid 50e18 WETH instead of 150e18 WETH
  Pool received correct 150e18 WETH (no pool loss)
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
