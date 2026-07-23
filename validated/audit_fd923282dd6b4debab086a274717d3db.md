### Title
Router Silently Drains Stranded ETH From Prior Callers During WETH Payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` unconditionally when settling a WETH-input swap. If the router holds ETH left over from a previous caller who forgot to include `refundETH` in their multicall, that ETH is silently consumed to subsidize the next user's WETH payment. The prior caller permanently loses their ETH with no revert or warning.

---

### Finding Description

In `PeripheryPayments.pay()`, when `token == WETH` and the router holds a non-zero native ETH balance that is less than the required `value`, the code executes the partial-ETH branch: [1](#0-0) 

```solidity
} else if (nativeBalance > 0) {
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
}
```

This branch is designed for the legitimate mixed-ETH+WETH pattern (user sends partial ETH with `msg.value` and the rest is pulled from their WETH balance). However, `address(this).balance` is a **persistent contract state** — it is not scoped to the current transaction. Any ETH left in the router from a prior call (e.g., a user who sent excess ETH and omitted `refundETH`) is indistinguishable from intentionally-provided ETH.

The `pay` function is invoked from the swap callback: [2](#0-1) 

The `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` entry points are all `payable`: [3](#0-2) [4](#0-3) 

A user can call any of these directly with excess ETH (e.g., `exactInputSingle{value: 2000}(amountIn=1000)`). The 1000 excess ETH remains in the router. The `receive()` guard only blocks direct ETH transfers from non-WETH addresses; it does not prevent ETH accumulation via `msg.value` on payable calls: [5](#0-4) 

The `refundETH` function exists to recover this ETH, but it is opt-in and not enforced: [6](#0-5) 

---

### Impact Explanation

**Direct loss of user ETH principal.** The stranded ETH is transferred to a pool as WETH on behalf of a different user's swap. The original depositor cannot recover it after it has been consumed. The beneficiary (the next WETH swapper) pays less WETH than they owe; the pool receives the correct total, so the pool itself is not harmed — but the prior caller's ETH is permanently redistributed without consent.

---

### Likelihood Explanation

The `multicall` pattern for ETH-input swaps is explicitly documented and tested in the codebase: [7](#0-6) 

Users following the pattern `multicall{value}([exactInputSingle, refundETH])` are safe. However:
- Users who call `exactInputSingle{value: X}(amountIn: Y)` directly with `X > Y` (a natural mistake) leave `X - Y` ETH stranded.
- Users who build a multicall but omit `refundETH` (e.g., when `amountIn` is an overestimate) leave excess ETH stranded.
- The stranded ETH is consumed silently by the very next WETH swap from any address, with no time window for recovery within the same block.

---

### Recommendation

Track the ETH provided for the current transaction in transient storage at the entry point, and use that tracked value (not `address(this).balance`) inside `pay`. On entry to any payable swap function, store `msg.value` in a transient slot. In `pay`, read only that transient amount as the available native ETH, and clear it after use. This scopes ETH availability to the transaction that provided it, preventing cross-caller contamination.

Alternatively, revert if `address(this).balance > msg.value` at the start of any payable entry point, ensuring no stale ETH can influence the current call.

---

### Proof of Concept

```
Setup:
  - Router deployed with WETH address.
  - Pool seeded with WETH/Token1 liquidity.
  - User A has 2000 ETH. User B has 1500 WETH approved to router.

Step 1 — User A strands ETH:
  UserA calls exactInputSingle{value: 2000}(tokenIn=WETH, amountIn=1000, ...)
  → pay() branch: nativeBalance=2000 >= value=1000
    → deposits 1000 ETH as WETH, sends to pool. ✓
  → 1000 ETH remains in router (UserA forgot refundETH).

Step 2 — User B exploits stranded ETH:
  UserB calls exactInputSingle(tokenIn=WETH, amountIn=1500, ...)  // no ETH sent
  → pool callback fires, pay(WETH, UserB, pool, 1500) called
  → nativeBalance = 1000 > 0, nativeBalance < 1500
  → deposits router's 1000 ETH as WETH → transfers 1000 WETH to pool
  → safeTransferFrom(UserB, pool, 500)  // pulls only 500 WETH from UserB

Result:
  UserA loses 1000 ETH (permanently consumed, unrecoverable).
  UserB pays only 500 WETH instead of 1500 WETH.
  Pool receives correct 1500 WETH total — no pool-level anomaly to detect.
``` [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-130)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L8-10)
```text
/// @dev Native ETH flows follow Uniswap v3-periphery multicall patterns:
///      - ETH input: multicall{value}(exactInput*) with WETH as tokenIn
///      - ETH output: swap WETH to router, then unwrapWETH9 in the same multicall
```
