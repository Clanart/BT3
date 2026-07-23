Audit Report

## Title
Stranded ETH from Prior User's Unrefunded Payable Call Is Consumed by Subsequent User's WETH Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay()` funds WETH wrapping from `address(this).balance` — the router's entire native ETH balance — with no per-caller accounting. Any ETH left in the router by a prior user who did not call `refundETH()` is silently consumed to settle a subsequent user's WETH swap, constituting a direct, irreversible loss of the prior user's principal.

## Finding Description

**Root cause — `pay()` reads the whole contract balance:**

In `PeripheryPayments.sol` lines 73–84, when `token == WETH`, the function reads `address(this).balance` without any restriction to the current transaction's `msg.value`:

```solidity
uint256 nativeBalance = address(this).balance;   // ALL ETH in router
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
``` [1](#0-0) 

**ETH accumulation path — `exactInputSingle` is `payable` with no automatic refund:**

`exactInputSingle` accepts arbitrary `msg.value` and performs no refund before returning. The callback fires mid-swap and calls `pay()` for only the amount the pool actually consumed (derived from swap deltas), leaving any excess `msg.value` stranded in the router: [2](#0-1) 

The callback handler passes the pool-reported owed amount as `value`, not `params.amountIn`: [3](#0-2) 

**`refundETH()` is opt-in and not enforced:**

`refundETH()` transfers the full contract balance to `msg.sender`, but it must be called explicitly. There is no automatic invocation at the end of any swap entry point: [4](#0-3) 

**`receive()` guard does not prevent accumulation:**

The `receive()` guard only blocks direct ETH transfers from non-WETH addresses. It does not prevent ETH from accumulating via excess `msg.value` in `payable` entry points: [5](#0-4) 

**Exploit flow:**
1. User A calls `exactInputSingle{value: 1 ether}(tokenIn=WETH, amountIn=0.5 ether, ...)`. The pool callback fires; `pay()` reads `nativeBalance = 1 ETH`, wraps 0.5 ETH, sends to pool. Router retains 0.5 ETH. User A does not call `refundETH()`.
2. User B calls `exactInputSingle{value: 0}(tokenIn=WETH, amountIn=0.5 ether, ...)`. The pool callback fires; `pay()` reads `nativeBalance = 0.5 ETH` (User A's remainder). Since `0.5 >= 0.5`, it wraps User A's ETH and sends it to the pool. User B's WETH allowance is never touched.

Result: User A loses 0.5 ETH with no recourse; User B receives a fully subsidized swap.

## Impact Explanation

Direct, irreversible loss of user principal. User A's ETH is consumed to settle User B's swap obligation. User A receives nothing in return. This is a concrete theft of funds meeting Sherlock High/Critical thresholds. The `payer` argument is ignored entirely when `nativeBalance >= value`, meaning the intended payer's WETH allowance is bypassed.

## Likelihood Explanation

`exactInputSingle` is the primary router entry point. Any user who sends `msg.value > amountIn` (a common defensive pattern to avoid partial-fill reverts) and omits `refundETH()` creates the precondition. An attacker can monitor the mempool for such transactions, or speculatively call `exactInputSingle{value: 0}(tokenIn=WETH, ...)` — if no ETH is present the call falls through to `safeTransferFrom` at no cost beyond gas. No privileged access, no malicious pool, and no non-standard token is required.

## Recommendation

Restrict the ETH-wrapping path to only the ETH that arrived with the current transaction by replacing `address(this).balance` with `msg.value` in `pay()`, or track per-caller deposited ETH in transient storage. Alternatively, automatically call `refundETH()` (or an internal equivalent) at the end of every `payable` entry point so no ETH can accumulate between calls.

## Proof of Concept

```
Setup: Router holds 0 ETH. WETH price = 1:1 ETH.

Step 1 — User A:
  exactInputSingle{value: 1 ether}(
    tokenIn = WETH, amountIn = 0.5 ether, recipient = userA, ...
  )
  → callback: pay(WETH, userA, pool, 0.5 ether)
  → nativeBalance = 1 ETH >= 0.5 ETH → wraps 0.5 ETH, sends to pool
  → Router.balance = 0.5 ETH (stranded)
  → User A does NOT call refundETH()

Step 2 — Attacker (User B):
  exactInputSingle{value: 0}(
    tokenIn = WETH, amountIn = 0.5 ether, recipient = userB, ...
  )
  → callback: pay(WETH, userB, pool, 0.5 ether)
  → nativeBalance = 0.5 ETH >= 0.5 ETH → wraps User A's ETH, sends to pool
  → User B's WETH.allowance[userB][router]: untouched
  → Router.balance = 0 ETH

Assert:
  userA.balance decreased by 1 ETH, received swap output worth 0.5 ETH only
  userB paid 0 ETH and 0 WETH for a 0.5 WETH swap
  userA's 0.5 ETH is permanently lost
```

A Foundry integration test can reproduce this by deploying the router with a mock WETH and mock pool, executing the two calls in sequence, and asserting final balances.

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-77)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
