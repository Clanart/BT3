### Title
Router `pay()` silently consumes any stranded native ETH as WETH input, enabling theft of prior-user ETH residue via `refundETH()` or free WETH swaps — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

---

### Summary

`PeripheryPayments.pay()` uses the router's **entire** native ETH balance to settle a WETH swap obligation before pulling WETH from the payer. Because `refundETH()` is a public, caller-unrestricted function that sends all ETH on the router to `msg.sender`, any ETH stranded on the router from a prior user's `msg.value` (e.g., a multicall that omitted `refundETH()`) can be (a) stolen outright by any caller, or (b) silently consumed to fund a different user's WETH swap without pulling from that user's wallet.

---

### Finding Description

In `PeripheryPayments.pay()`, when `token == WETH` and `payer != address(this)`, the function reads `address(this).balance` and branches:

```
nativeBalance >= value  →  wrap router ETH, transfer WETH to pool  (payer's WETH never pulled)
nativeBalance > 0       →  wrap router ETH, pull remainder from payer
nativeBalance == 0      →  pull full amount from payer
``` [1](#0-0) 

In the first branch the payer's WETH is **never** pulled. This is intentional when the user sends `msg.value` in the same multicall — the ETH they sent is used to pay the pool. However, `address(this).balance` is **not scoped to the current caller or the current transaction's `msg.value`**. It includes any ETH left on the router from any prior transaction.

`refundETH()` is public, has no access control, and sends the router's entire ETH balance to `msg.sender`: [2](#0-1) 

The `receive()` guard only blocks plain ETH transfers from non-WETH senders: [3](#0-2) 

But ETH arrives via `msg.value` on any `payable` function (`multicall`, `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `unwrapWETH9`, `sweepToken`, `refundETH`). If a user sends more ETH than the swap consumes and omits `refundETH()`, the surplus persists on the router across transaction boundaries.

The `exactInputSingle` entry point sets the callback context to `payer = msg.sender`: [4](#0-3) 

When the pool calls `metricOmmSwapCallback`, `_justPayCallback` calls `pay(tokenIn, payer, pool, amount)`. If `tokenIn == WETH` and the router holds stranded ETH ≥ `amount`, the pool is paid from the stranded ETH and the attacker's WETH is never pulled. [5](#0-4) 

---

### Impact Explanation

**Attack vector A — direct ETH theft:**
Any caller invokes `refundETH()` after ETH is stranded. The entire router ETH balance is transferred to the attacker. The original depositor loses their ETH with no recourse.

**Attack vector B — free WETH swap:**
Attacker calls `exactInputSingle` with `tokenIn = WETH`, `amountIn = X` (no `msg.value`, no WETH approval required). If the router holds ≥ X stranded ETH, `pay()` wraps that ETH and sends WETH to the pool. The attacker receives the swap output without spending any of their own assets. The victim who stranded the ETH effectively subsidises the attacker's trade.

Both vectors constitute direct loss of user principal above Sherlock thresholds for any non-trivial ETH amount.

---

### Likelihood Explanation

ETH stranding is a realistic, recurring condition:

1. The test suite itself demonstrates the pattern — `test_multicall_ethInput_exactInputSingle_refundsUnusedEth` shows that users are expected to append `refundETH()` manually; omitting it is a one-call mistake.
2. Aggregators and wallets that batch `exactInputSingle` with ETH input may omit the refund step.
3. A partial fill (price limit hit before full `amountIn` is consumed) leaves ETH on the router even when the user intended to spend it all.
4. The attacker needs no special permissions, no token approvals, and no privileged role — only the ability to call a public function.

---

### Recommendation

1. **Track `msg.value` in transient storage** at multicall entry and deduct only the current-call allocation in `pay()`, rather than reading the raw `address(this).balance`.
2. **Scope `refundETH()` to the current `msg.sender`'s tracked allocation** rather than the entire contract balance, or make it callable only within a multicall context where the caller's ETH contribution is known.
3. Alternatively, document and enforce (via NatSpec and integration guides) that every ETH-input multicall **must** end with `refundETH()`, and add a revert guard if ETH remains after the last call.

---

### Proof of Concept

```
// Step 1: Victim strands ETH on the router
victim calls:
  router.multicall{value: 1 ether}([
    abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams{
      pool: wethPool,
      tokenIn: WETH,
      amountIn: 0.5 ether,
      ...
    }))
    // refundETH() omitted
  ]);
// Result: 0.5 ETH remains on router

// Step 2A — Theft via refundETH
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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
