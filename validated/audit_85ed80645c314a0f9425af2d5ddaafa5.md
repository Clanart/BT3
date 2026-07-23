Audit Report

## Title
Partial-ETH branch in `pay()` silently consumes stranded router ETH to subsidize a subsequent WETH swap — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay()` helper in `PeripheryPayments.sol` contains a middle branch (lines 78–81) that fires when the router holds any ETH less than the required payment amount. It unconditionally drains the router's entire ETH balance as a partial payment toward a WETH swap, then pulls only the remainder from the actual payer's allowance. Because the router's ETH balance carries no per-user attribution, any ETH stranded by a prior user (who sent `msg.value` but omitted `refundETH()`) is silently consumed by the next WETH swap, causing direct, irreversible loss of principal to the original depositor.

## Finding Description
The vulnerable code is confirmed at `metric-periphery/contracts/base/PeripheryPayments.sol` lines 69–88:

```solidity
function pay(address token, address payer, address recipient, uint256 value) internal {
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
        uint256 nativeBalance = address(this).balance;
        if (nativeBalance >= value) {
            IWETH9(WETH).deposit{value: value}();
            IERC20(WETH).safeTransfer(recipient, value);
        } else if (nativeBalance > 0) {                          // ← vulnerable branch
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
``` [1](#0-0) 

The call chain that reaches this branch is:

1. `exactInputSingle` (payable, line 67) stores `msg.sender` as payer in transient storage via `_setNextCallbackContext` (line 71).
2. The pool calls back `metricOmmSwapCallback`, which dispatches to `_justPayCallback` (lines 53–55).
3. `_justPayCallback` calls `pay(_getTokenToPay(), _getPayer(), msg.sender, amount)` (lines 192–199). [2](#0-1) 

When `tokenToPay == WETH` and the router holds any residual ETH (from a prior user's unrefunded `msg.value`), the middle branch fires: it wraps and sends that ETH to the pool, then pulls only the shortfall from the current payer's allowance. The router's `receive()` guard (line 32–34) only prevents direct ETH sends from non-WETH addresses; it does not prevent ETH accumulation via `msg.value` in `payable` entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`, etc.). [3](#0-2) 

## Impact Explanation
User A sends `msg.value = 200e18` with `amountIn = 100e18` WETH. The first-branch fires (`nativeBalance >= value`), wrapping exactly 100e18 ETH. The remaining 100e18 ETH stays on the router. If User A omits `refundETH()`, that ETH is stranded. User B then calls `exactInputSingle` with `tokenIn = WETH`, `amountIn = 150e18`. In the callback, `nativeBalance = 100e18 > 0` but `< 150e18`, so the middle branch fires: 100e18 ETH is wrapped and sent to the pool, and only 50e18 WETH is pulled from User B. User A suffers a direct, irreversible loss of 100e18 ETH. The pool is made whole (receives correct 150e18 WETH), so no pool insolvency occurs, but User A's principal is permanently transferred to User B's benefit. This is a direct loss of user principal meeting Critical/High severity thresholds.

## Likelihood Explanation
- `refundETH()` is not enforced by any entry point; it must be manually composed into a multicall, making omission common.
- All four swap entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) are `payable` and can leave residual ETH on the router.
- The exploit requires no privileged access: any address can call `exactInputSingle` with `tokenIn = WETH` to trigger the partial-ETH branch.
- The router's ETH balance is publicly readable on-chain, enabling an attacker to monitor and time the call immediately after a victim's transaction.

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
  After swap: router.balance = 100e18  (User A forgot refundETH)

Step 2 – User B exploits:
  User B calls exactInputSingle(
      pool, tokenIn=WETH, amountIn=150e18, ...
  )
  Callback: pay(WETH, UserB, pool, 150e18)
    nativeBalance=100e18 > 0, < 150e18 → partial branch fires:
      wraps 100e18 ETH → sends to pool
      safeTransferFrom(UserB, pool, 50e18)  ← only 50e18 pulled from User B

Result:
  User A lost 100e18 ETH (stranded, now consumed)
  User B paid 50e18 WETH instead of 150e18 WETH
  Pool received correct 150e18 WETH (no pool loss)
```

A Foundry test can reproduce this by deploying the router with a mock WETH and pool, executing Step 1 without `refundETH()`, then executing Step 2 and asserting `router.balance == 0` and User B's WETH deduction equals only 50e18.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L78-81)
```text
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
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
