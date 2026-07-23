### Title
Stranded ETH on Router Consumed by Subsequent WETH Swapper — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses the router's **total** native ETH balance as a funding source for any WETH payment, with no per-user attribution. ETH stranded from a prior user's `multicall` (who omitted `refundETH`) is silently consumed by the next caller whose `tokenIn` is WETH, causing direct loss of the victim's principal.

---

### Finding Description

`pay()` contains the following WETH branch: [1](#0-0) 

```solidity
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

`address(this).balance` is the **router-wide** ETH balance, not scoped to the current caller's transaction. ETH arrives on the router via `payable` function calls (e.g., `multicall{value: X}(...)`) — this bypasses the `receive()` guard which only blocks direct transfers: [2](#0-1) 

When a user sends `multicall{value: 1 ETH}([exactInputSingle(WETH, amountIn=0.9 ETH)])` without appending `refundETH`, 0.1 ETH remains on the router after the swap. The next independent caller who invokes `exactInputSingle(WETH, amountIn=0.1 ETH)` triggers `pay(WETH, attacker, pool, 0.1 ETH)`. Since `nativeBalance (0.1 ETH) >= value (0.1 ETH)`, the router deposits the victim's stranded ETH as WETH and forwards it to the pool — the attacker pays **zero WETH from their own wallet**.

The callback path that reaches `pay()` is: [3](#0-2) 

`_justPayCallback` → `pay(_getTokenToPay(), _getPayer(), msg.sender, ...)` — `_getPayer()` returns the attacker's address, but `pay()` never pulls from that address when `nativeBalance >= value`.

---

### Impact Explanation

Direct loss of user principal. Any ETH stranded on the router (from any prior user who omitted `refundETH`) is permanently claimable by the next WETH swapper. The victim receives no output tokens for their stranded ETH; the attacker receives a full swap at zero cost. Impact is **High** under Sherlock thresholds (direct loss of user funds above dust).

---

### Likelihood Explanation

**Medium-High.** The multicall + WETH + `refundETH` pattern is explicitly documented as the intended ETH input flow: [4](#0-3) 

Omitting `refundETH` is a well-known user error (identical footgun exists in Uniswap v3). An MEV bot watching the mempool can front-run or back-run any transaction that leaves ETH on the router. No special privileges, malicious pools, or non-standard tokens are required — only two sequential public calls.

---

### Recommendation

Scope the native ETH balance to the **current `msg.value`** rather than `address(this).balance`. One approach: pass `msg.value` (or a per-call ETH budget) into `pay()` and consume only up to that amount, reverting or ignoring any pre-existing router balance. Alternatively, enforce that `address(this).balance` equals `msg.value` at the start of each top-level entry point (feasible via transient storage tracking), or require callers to always include `refundETH` by checking residual balance at the end of `multicall`.

---

### Proof of Concept

```
1. User A: router.multicall{value: 1 ETH}([
       exactInputSingle(tokenIn=WETH, amountIn=0.9 ETH, ...)
       // no refundETH
   ])
   → router.balance == 0.1 ETH after call

2. User B (attacker): router.exactInputSingle{value: 0}(
       tokenIn=WETH, amountIn=0.1 ETH, ...
   )
   → pay(WETH, userB, pool, 0.1 ETH) fires
   → nativeBalance (0.1 ETH) >= value (0.1 ETH)
   → router deposits victim's 0.1 ETH as WETH, transfers to pool
   → userB pulls 0 WETH from their own wallet

Assert: router.balance == 0, userA's 0.1 ETH is gone, userB received full swap output.
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L15-17)
```text
/// @dev Native ETH input uses the same multicall pattern as the swap router: send ETH with the add call (or
///      `multicall{value}`) when the pool's WETH leg is token0 or token1; unused ETH can be reclaimed via
///      `refundETH` in the same multicall.
```
