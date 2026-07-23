### Title
`pay()` consumes unattributed router ETH balance to settle any WETH swap, draining stranded ETH from prior callers — (`File: metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` — the router's total native ETH balance — to settle WETH-input swaps. Because this balance is never attributed to a specific caller, any ETH left on the router from a prior payable call (e.g., a `multicall{value: X}` that did not include `refundETH()`) is silently consumed by the next user who executes a WETH-input swap. The victim loses principal; the beneficiary pays nothing from their own wallet.

---

### Finding Description

`PeripheryPayments.pay()` contains the following WETH branch:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;          // ← total router ETH, no attribution
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);        // payer pays nothing
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance); // payer pays remainder
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
``` [1](#0-0) 

The design intent is that a user sends ETH as `msg.value` in a `multicall`, and `pay()` wraps it to WETH on their behalf. However, `address(this).balance` is the **aggregate** router balance — it includes ETH from any prior payable call that was not refunded. There is no per-caller accounting.

The `receive()` guard only blocks direct ETH pushes:

```solidity
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
``` [2](#0-1) 

But `msg.value` in any `payable` function bypasses `receive()`. All of `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`, `unwrapWETH9`, and `sweepToken` are `payable`. ETH sent with any of these calls and not consumed remains on the router across transaction boundaries.

`_justPayCallback` feeds `pay()` with `payer = msg.sender` (the current swapper) and `token = WETH`:

```solidity
function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
        _getTokenToPay(),
        _getPayer(),
        msg.sender,
        uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
}
``` [3](#0-2) 

When `address(this).balance >= value`, the payer's `safeTransferFrom` is never called — the router's pooled ETH is wrapped and forwarded instead.

A parallel theft path exists via `refundETH()`, which is public and sends the **entire** router ETH balance to `msg.sender` with no attribution check:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [4](#0-3) 

Any caller can invoke `refundETH()` standalone to claim all stranded ETH.

---

### Impact Explanation

**Direct loss of user principal.** A user who sends ETH in a payable router call and omits `refundETH()` from their multicall permanently loses the unspent ETH. A subsequent unprivileged caller recovers it either by:

1. Calling `refundETH()` directly — receives the full stranded balance.
2. Calling `exactInputSingle` (or `exactInput`) with `tokenIn = WETH` — the `pay()` function silently wraps the stranded ETH and forwards it to the pool, so the attacker's own WETH allowance is never touched.

In both cases the victim's ETH is transferred to a third party with no recourse.

---

### Likelihood Explanation

The pattern of sending `msg.value` to a router and relying on `refundETH()` at the end of a multicall is standard DeFi UX (Uniswap v3, etc.). Users and integrators routinely omit the refund step, especially when building multicalls programmatically or when a revert in a later step causes them to retry without the refund call. The router's `multicall` uses `delegatecall`, so any step that reverts mid-flight leaves the ETH on the router. The exploit requires no special privilege — a single public call suffices.

---

### Recommendation

Track the ETH contributed by the current top-level call in transient storage (e.g., store `msg.value` at `multicall`/swap entry and decrement as it is consumed by `pay()`). In `pay()`, replace `address(this).balance` with the transient per-call ETH budget. Alternatively, require callers to wrap ETH to WETH before calling the router and remove the native-ETH branch from `pay()` entirely, relying solely on `safeTransferFrom`.

---

### Proof of Concept

```
1. Alice calls router.multicall{value: 1 ether}([
       exactInputSingle(tokenIn=WETH, amountIn=1000, ...)   // uses 1000 wei of ETH
       // refundETH() omitted
   ])
   → 1 ether - 1000 wei = ~1 ETH stranded on router.

2. Bob calls router.exactInputSingle(tokenIn=WETH, amountIn=500_000, ...)
   → _justPayCallback fires: pay(WETH, Bob, pool, 500_000)
   → address(this).balance = ~1 ETH >= 500_000
   → router wraps 500_000 wei of Alice's ETH and sends WETH to pool
   → Bob's safeTransferFrom is never called; Bob pays 0 from his wallet.

3. Alternatively, Bob calls router.refundETH()
   → receives the full ~1 ETH directly.

Alice loses ~1 ETH. Bob gains it. No privileged access required.
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
