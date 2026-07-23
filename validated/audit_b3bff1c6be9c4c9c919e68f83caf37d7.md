Audit Report

## Title
Router `pay()` consumes unscoped contract ETH balance and `refundETH()` has no access control, enabling stranded-ETH theft and free WETH swaps — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
`PeripheryPayments.pay()` reads `address(this).balance` — the router's entire native ETH balance — when settling a WETH swap, rather than only the ETH contributed by the current caller. Separately, `refundETH()` is a public, unrestricted function that forwards the router's entire ETH balance to `msg.sender`. Any ETH stranded on the router from a prior user's `msg.value` (e.g., a multicall that omitted `refundETH()`) can be (A) stolen outright by any caller via `refundETH()`, or (B) silently consumed to fund a different user's WETH swap without pulling from that user's wallet.

## Finding Description

**Root cause — `pay()` reads unscoped balance:**

In `PeripheryPayments.pay()`, when `token == WETH` and `payer != address(this)`, the function branches on `address(this).balance`:

```solidity
// metric-periphery/contracts/base/PeripheryPayments.sol L73-84
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // payer's WETH never pulled
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

`address(this).balance` is not scoped to the current caller or the current transaction's `msg.value`. It includes any ETH left on the router from any prior transaction. [1](#0-0) 

**Root cause — `refundETH()` has no access control:**

```solidity
// metric-periphery/contracts/base/PeripheryPayments.sol L58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

Any address can call this and receive the router's entire ETH balance. [2](#0-1) 

**ETH stranding mechanism:**

The `receive()` guard only blocks plain ETH transfers from non-WETH senders:

```solidity
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
```

But ETH arrives via `msg.value` on any `payable` function. `multicall` is `payable` and uses `delegatecall`, so `msg.value` is preserved across all sub-calls. If a user sends more ETH than the swap consumes and omits `refundETH()`, the surplus persists on the router across transaction boundaries. [3](#0-2) 

**Exploit call path for Vector B:**

`exactInputSingle` sets `payer = msg.sender` via `_setNextCallbackContext`: [4](#0-3) 

The pool calls `metricOmmSwapCallback`, which dispatches to `_justPayCallback`: [5](#0-4) 

If `tokenIn == WETH` and the router holds stranded ETH ≥ `amountIn`, `pay()` wraps that ETH and sends WETH to the pool — the attacker's WETH is never pulled and no approval is required.

## Impact Explanation

**Vector A — Direct ETH theft (High/Critical):** Any caller invokes `refundETH()` after ETH is stranded on the router. The entire router ETH balance is transferred to the attacker. The original depositor loses their ETH with no recourse. This is direct loss of user principal with no lower bound on amount.

**Vector B — Free WETH swap (High):** An attacker calls `exactInputSingle` with `tokenIn = WETH` and `amountIn = X` (no `msg.value`, no WETH approval). If the router holds ≥ X stranded ETH, `pay()` wraps that ETH and sends WETH to the pool. The attacker receives the swap output without spending any of their own assets. The victim who stranded the ETH effectively subsidises the attacker's trade. Both impacts constitute direct loss of user principal above Sherlock thresholds.

## Likelihood Explanation

ETH stranding is a realistic, recurring condition:
1. `multicall` is `payable` and the expected usage pattern (ETH-input swaps) requires users to manually append `refundETH()`. Omitting it is a single-call mistake.
2. Aggregators and wallets batching `exactInputSingle` with ETH input may omit the refund step.
3. A partial fill (price limit hit before full `amountIn` is consumed) leaves ETH on the router even when the user intended to spend it all.
4. The attacker needs no special permissions, no token approvals, and no privileged role — only the ability to call a public function.

## Recommendation

1. **Track `msg.value` in transient storage** at `multicall` entry and deduct only the current-call allocation in `pay()`, rather than reading the raw `address(this).balance`.
2. **Scope `refundETH()` to the current `msg.sender`'s tracked ETH allocation** rather than the entire contract balance, or restrict it to within a multicall context where the caller's ETH contribution is known.
3. Alternatively, enforce via a revert guard that every ETH-input multicall ends with `refundETH()`, and revert if ETH remains after the last call.

## Proof of Concept

```solidity
// Step 1: Victim strands ETH on the router
victim calls:
  router.multicall{value: 1 ether}([
    abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams{
      pool: wethPool,
      tokenIn: WETH,
      amountIn: 0.5 ether,
      ...
    }))
    // refundETH() omitted — 0.5 ETH remains on router
  ]);

// Step 2A — Theft via refundETH (no msg.value, no approval needed)
attacker calls:
  router.refundETH();
// Result: attacker receives 0.5 ETH; victim's ETH is gone

// Step 2B — Free swap via pay() ETH-first branch
attacker calls (no msg.value, no WETH approval):
  router.exactInputSingle(ExactInputSingleParams{
    pool: wethPool,
    tokenIn: WETH,
    amountIn: 0.5 ether,
    recipient: attacker,
    ...
  });
// In callback: pay(WETH, attacker, pool, 0.5 ether)
//   nativeBalance (0.5 ETH) >= value (0.5 ETH)
//   → router wraps 0.5 ETH, sends WETH to pool
//   → attacker's WETH never pulled
// Result: attacker receives swap output for free
```

A Foundry test can reproduce this by: (1) deploying the router with a mock WETH and pool, (2) having a victim call `multicall{value: 1 ether}` with a single `exactInputSingle` and no `refundETH`, (3) asserting `address(router).balance == 0.5 ether`, then (4a) having an attacker call `refundETH()` and asserting they received 0.5 ETH, or (4b) having an attacker call `exactInputSingle` with no approval and asserting the pool received WETH funded from the router's balance.

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
