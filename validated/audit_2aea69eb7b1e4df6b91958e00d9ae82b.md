Audit Report

## Title
Partial-ETH branch in `pay()` silently consumes stranded router ETH to subsidize a subsequent WETH swap — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay()` helper in `PeripheryPayments.sol` contains a middle branch (lines 78–81) that fires when the router holds any ETH less than the required payment amount. It unconditionally wraps and spends the router's entire ETH balance as a partial payment, then pulls only the remainder from the actual payer's allowance. Because the router's ETH balance carries no per-user attribution, any ETH stranded by a prior user (who sent `msg.value` but omitted `refundETH()`) is silently consumed by the next WETH swap, causing direct, irreversible loss of principal to the original depositor.

## Finding Description
`pay()` in `PeripheryPayments.sol` (lines 69–88) has three branches when `token == WETH` and `payer != address(this)`:

```
nativeBalance >= value  →  wrap value ETH, pull nothing from payer          (line 75–77)
nativeBalance > 0       →  wrap nativeBalance ETH, pull remainder from payer (line 78–81) ← root cause
nativeBalance == 0      →  pull all from payer                               (line 82–84)
```

The middle branch unconditionally drains `address(this).balance` regardless of which user deposited it. The callback chain that reaches `pay()` is:

```
exactInputSingle (payable, line 67)
  → _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, msg.sender, tokenIn)  (line 71)
  → pool.swap(...)
  → metricOmmSwapCallback(...)  (line 46)
  → _justPayCallback(...)  (line 192)
  → pay(tokenToPay, _getPayer(), msg.sender /*pool*/, amount)  (line 193–198)
```

`_getPayer()` returns the original `msg.sender` stored in transient storage. When `tokenToPay == WETH` and the router holds any residual ETH, the middle branch fires: it wraps and sends that ETH to the pool, then pulls only `value - nativeBalance` WETH from the actual payer. The router's `receive()` function (line 32–34) only blocks plain ETH transfers from non-WETH addresses; it does not prevent ETH accumulation via `msg.value` on payable entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`, `unwrapWETH9`, `sweepToken`). No guard checks whether the router's ETH balance belongs to the current caller before consuming it.

## Impact Explanation
Direct, irreversible loss of user principal. User A sends `msg.value = V` to a payable entry point, swapping `A < V` worth of WETH. After the swap, `V − A` ETH remains on the router. If User A omits `refundETH()`, that ETH is permanently stranded. User B then calls `exactInputSingle` with `tokenIn = WETH`, `amountIn = X` where `X > V − A`. In the callback, `pay()` sees `nativeBalance = V − A > 0`, wraps it, sends it to the pool, then pulls only `X − (V − A)` WETH from User B's allowance. User B receives the full swap output while paying `V − A` less than owed. User A's `V − A` ETH is permanently lost. The pool is made whole (no pool insolvency), but User A suffers a direct loss equal to the stranded ETH amount. This meets the Sherlock threshold for a direct loss of user principal.

## Likelihood Explanation
`refundETH()` is not enforced by the router and must be manually composed into a multicall — a common omission in direct calls or incomplete multicalls. Any payable entry point can receive `msg.value` and leave residue. The exploit requires no privileged access: any unprivileged address can call `exactInputSingle` with `tokenIn = WETH` to trigger the partial-ETH branch. An attacker can monitor the router's ETH balance on-chain and time the call immediately after a victim's transaction, making the attack repeatable and low-cost.

## Recommendation
Replace the partial-ETH branch with the Uniswap v3 pattern: only use router ETH when it fully covers the payment; otherwise fall through to `safeTransferFrom`:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

This eliminates cross-user ETH consumption while preserving the intended "pay with native ETH" convenience for users who send exactly the right `msg.value`.

## Proof of Concept
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
  After swap: router.balance = 100e18  (User A omits refundETH)

Step 2 – User B exploits:
  User B calls exactInputSingle(
      pool, tokenIn=WETH, amountIn=150e18, ...
  )
  Callback: pay(WETH, UserB, pool, 150e18)
    nativeBalance=100e18 > 0, < 150e18 → partial branch fires (line 78–81):
      wraps 100e18 ETH → sends to pool
      safeTransferFrom(UserB, pool, 50e18)  ← only 50e18 pulled from User B

Result:
  User A lost 100e18 ETH (stranded, now consumed by User B's swap)
  User B paid 50e18 WETH instead of 150e18 WETH
  Pool received correct 150e18 WETH (no pool loss)
```

Foundry test plan: deploy router with a mock WETH and mock pool that calls back with the expected deltas; fund the router with 100e18 ETH directly via a payable call; call `exactInputSingle` as User B with `amountIn = 150e18` and `tokenIn = WETH`; assert that `safeTransferFrom` was called for only 50e18 and that `router.balance == 0`. [1](#0-0) [2](#0-1) [3](#0-2)

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
