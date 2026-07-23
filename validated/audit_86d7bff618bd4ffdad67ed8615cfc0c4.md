### Title
`pay()` WETH Zero-Native-Balance Branch Uses `safeTransfer` Instead of `safeTransferFrom`, Allowing Router-Held WETH Theft and Breaking Pure-WETH Swaps — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` has three branches for WETH payments. The third branch — triggered when the router holds **no native ETH** — calls `IERC20(WETH).safeTransfer(recipient, value)` (transferring from the router's own WETH balance) instead of `IERC20(WETH).safeTransferFrom(payer, recipient, value)` (pulling from the user). This means any WETH-input swap that sends zero native ETH either reverts (if the router holds no WETH) or silently drains router-held WETH to pay the pool (if stranded WETH is present), giving the caller free swap output.

---

### Finding Description

`PeripheryPayments.pay()` handles WETH settlement with three branches: [1](#0-0) 

```solidity
} else if (token == WETH) {
  uint256 nativeBalance = address(this).balance;
  if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);       // ✓ wraps native ETH
  } else if (nativeBalance > 0) {
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance); // ✓ hybrid
  } else {
    IERC20(WETH).safeTransfer(recipient, value);       // ✗ BUG: should be safeTransferFrom
  }
```

The `else` branch (line 83) fires when `nativeBalance == 0`. It calls `safeTransfer(recipient, value)`, which transfers from the **router's own WETH balance**, not from `payer`. The correct call is `safeTransferFrom(payer, recipient, value)`.

This is called from `_justPayCallback` during every swap callback: [2](#0-1) 

The payer for the first hop is always `msg.sender` (the swapper): [3](#0-2) 

When a user calls `exactInputSingle` with `tokenIn = WETH` and sends **no native ETH**, the `else` branch fires. The router attempts `safeTransfer(pool, value)` from its own WETH balance. The user's WETH allowance is never consumed.

---

### Impact Explanation

**Scenario A — Broken WETH-input swaps (DoS):** Any user who approves WETH to the router and calls `exactInputSingle`/`exactInput` with `tokenIn = WETH` without attaching native ETH will have their transaction revert (router has no WETH to transfer). The user's WETH is never pulled. Core swap functionality is broken for the standard ERC-20 WETH approval flow.

**Scenario B — Theft of stranded WETH:** WETH can become stranded on the router legitimately:
- A user does `exactInput(token→WETH, recipient=router)` + `unwrapWETH9` in a multicall, but the unwrap step is omitted or fails.
- Any partial-fill or revert mid-multicall that leaves WETH on the router.

Once WETH is stranded, an attacker calls `exactInputSingle(WETH→anyToken, amountIn=stranded_amount, no ETH)`. The `else` branch fires, the router transfers its own WETH to the pool, and the attacker receives the output tokens for free. The victim's stranded WETH is permanently lost.

---

### Likelihood Explanation

- **Scenario A** is triggered by any user who follows the standard ERC-20 approval pattern for WETH (approve + call, no ETH attached). This is a common integration pattern.
- **Scenario B** requires WETH to be stranded on the router first, which can happen through normal multicall usage (e.g., token→WETH swap with `recipient=router` followed by a failed or missing `unwrapWETH9`). Once stranded, any attacker can steal it in a single transaction.

Both scenarios are reachable by unprivileged callers with no special setup beyond normal protocol usage.

---

### Recommendation

Replace the `else` branch in `PeripheryPayments.pay()` with `safeTransferFrom`:

```solidity
} else {
  // No native ETH: pull WETH directly from payer
  IERC20(WETH).safeTransferFrom(payer, recipient, value);
}
```

This matches the intent of the hybrid branch above it and the non-WETH ERC-20 branch below it. [4](#0-3) 

---

### Proof of Concept

1. Alice does a `token1→WETH` swap via `exactInputSingle` with `recipient = address(router)`, receiving 1000 WETH stranded on the router (e.g., she forgot to include `unwrapWETH9` in her multicall).

2. Bob observes the router's WETH balance on-chain.

3. Bob calls:
   ```solidity
   router.exactInputSingle(ExactInputSingleParams({
     pool: weth_token1_pool,
     tokenIn: address(weth),
     tokenOut: address(token1),
     zeroForOne: true,
     amountIn: 1000,          // matches stranded WETH
     amountOutMinimum: 0,
     recipient: bob,
     deadline: block.timestamp + 1,
     priceLimitX64: 0,
     extensionData: ""
   }));
   // Bob sends 0 ETH with this call
   ```

4. Inside `metricOmmSwapCallback`, `pay(WETH, bob, pool, 1000)` is called. `nativeBalance == 0`, so the `else` branch fires: `IERC20(WETH).safeTransfer(pool, 1000)` — the router's own WETH is sent to the pool.

5. The pool sends token1 to Bob. Bob receives token1 output without spending any WETH. Alice's 1000 WETH is permanently lost.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-88)
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
