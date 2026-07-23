### Title
Stranded native ETH on the router is silently consumed by any subsequent WETH-input swap, enabling theft of prior users' funds - (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
The `pay()` helper in `PeripheryPayments` uses `address(this).balance` — the router's **total** native ETH balance — when settling a WETH-input swap. Because that balance persists across transactions, any ETH left on the router by a prior user (who sent `msg.value` but omitted a `refundETH` step) is silently consumed to pay for a later caller's swap. The later caller receives pool output without spending any WETH or ETH of their own, while the original depositor permanently loses their stranded ETH.

### Finding Description

`PeripheryPayments.pay()` contains three branches for the `token == WETH` case:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);          // ← no pull from payer
} else if (nativeBalance > 0) {
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance); // partial pull
} else {
    IERC20(WETH).safeTransferFrom(payer, recipient, value);
}
``` [1](#0-0) 

In both the `>=` and the `> 0` branches, the router wraps and forwards **its own native ETH balance** rather than pulling WETH from the payer. The balance queried is `address(this).balance`, which is the router's **cumulative** ETH balance across all past transactions, not just `msg.value` from the current call.

The `receive()` guard only blocks unsolicited ETH pushes from non-WETH addresses:

```solidity
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
``` [2](#0-1) 

It does **not** prevent ETH from accumulating via `msg.value` on any `payable` entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`, `unwrapWETH9`, `sweepToken`, `refundETH` — all are `payable`). A user who sends `msg.value = X` for a WETH swap that only consumes `Y < X` leaves `X - Y` ETH stranded on the router if they omit a `refundETH` call.

The transient callback context (`TransientCallbackPool`) correctly binds the expected pool and payer for the current swap:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
``` [3](#0-2) 

But the payer identity stored in transient storage is irrelevant once `nativeBalance >= value`: `pay()` never calls `safeTransferFrom` in that branch, so the payer's WETH allowance is never checked or consumed. The router simply wraps whatever ETH it holds and forwards it.

### Impact Explanation

**Direct theft of user principal.** Any ETH stranded on the router — from any prior user's unrefunded `msg.value` — is available to the next caller who issues a WETH-input swap with `amountIn ≤ stranded_amount`. That caller:

- Receives pool output tokens of full market value.
- Spends zero WETH and zero ETH of their own.
- Requires no WETH approval.

The original depositor permanently loses the stranded ETH with no recourse. The loss is bounded only by how much ETH is stranded at the time of the attack, which an attacker can observe on-chain before acting.

### Likelihood Explanation

**Medium.** ETH stranding is a realistic outcome of normal usage:

1. A user calls `exactInputSingle` with `tokenIn = WETH` and sends `msg.value` to cover the swap, but the pool partially fills (price limit hit) or the user over-estimates `amountIn`.
2. The user omits `refundETH` from their multicall — a common mistake when composing calls off-chain or when a UI does not append the refund step.
3. An attacker monitors the router's ETH balance (a single `eth_getBalance` RPC call) and immediately exploits any non-zero balance.

The attack requires no special permissions, no flash loan, and no privileged role.

### Recommendation

Track only the ETH attributable to the **current transaction** for WETH settlement. One approach: record `msg.value` at the entry point of each swap function and pass it explicitly to `pay()`, using only that amount for native-ETH wrapping. Alternatively, after each swap, assert `address(this).balance == 0` (or equal to the pre-call balance) so that any unspent ETH is detected and refunded atomically rather than left for a future caller to consume.

### Proof of Concept

```
Block N:
  User A calls exactInputSingle{value: 1 ETH}(
      tokenIn = WETH, amountIn = 0.5 ETH, ...
  )
  → pay() sees nativeBalance = 1 ETH ≥ 0.5 ETH
  → deposits 0.5 ETH, transfers 0.5 WETH to pool
  → 0.5 ETH remains on router
  User A does NOT call refundETH.

Block N+1:
  Attacker calls exactInputSingle{value: 0}(
      tokenIn = WETH, amountIn = 0.5 ETH, ...
  )
  → pay() sees nativeBalance = 0.5 ETH ≥ 0.5 ETH
  → deposits 0.5 ETH (User A's), transfers 0.5 WETH to pool
  → safeTransferFrom is NEVER called; attacker needs zero WETH approval
  → Attacker receives pool output worth ~0.5 ETH for free
  → User A's 0.5 ETH is gone
```

Relevant code path: `exactInputSingle` → `_setNextCallbackContext` → `pool.swap` → `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, attacker, pool, 0.5e18)`. [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

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
