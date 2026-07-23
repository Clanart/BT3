Audit Report

## Title
Router Silently Drains Stranded ETH From Prior Callers During WETH Payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay()` reads `address(this).balance` as the available native ETH when settling a WETH-input swap. Because this is persistent contract state rather than the current transaction's `msg.value`, any ETH left in the router by a prior caller (who sent excess ETH and omitted `refundETH`) is silently consumed to subsidize the next user's WETH payment. The prior caller permanently loses their ETH with no revert or warning.

## Finding Description

In `pay()`, when `token == WETH`, the function reads `address(this).balance` unconditionally: [1](#0-0) 

`address(this).balance` is not scoped to the current transaction — it reflects the total ETH held by the contract across all callers. ETH accumulates in the router whenever a user calls any payable entry point with `msg.value` exceeding the actual swap input. The `receive()` guard only blocks direct ETH transfers from non-WETH addresses: [2](#0-1) 

It does not prevent ETH accumulation via `msg.value` on payable calls. All four swap entry points are `payable`: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

`refundETH` exists to recover stranded ETH but is opt-in and not enforced: [7](#0-6) 

The callback path that invokes `pay()` is: [8](#0-7) 

When User B calls `exactInputSingle(tokenIn=WETH, amountIn=1500)` with no ETH sent, and the router holds 1000 ETH from User A's prior excess, the `nativeBalance > 0` branch fires: it deposits and transfers User A's 1000 ETH as WETH to the pool, then pulls only 500 WETH from User B via `safeTransferFrom`. User A's 1000 ETH is permanently consumed.

## Impact Explanation

Direct loss of user ETH principal. The stranded ETH is transferred to a pool as WETH on behalf of a different user's swap. The original depositor cannot recover it after it has been consumed. The beneficiary pays less WETH than owed; the pool receives the correct total, so there is no pool-level anomaly to detect. This constitutes a Critical/High direct loss of user principal above Sherlock thresholds.

## Likelihood Explanation

Any user who calls a payable swap function with `msg.value` exceeding `amountIn` (a natural mistake, e.g., sending a round number for safety) strands the excess. Any subsequent WETH-input swap from any address in the same or a later block silently consumes it. No attacker capability is required — the consuming swap need not be malicious; it is triggered by normal usage. The stranded ETH is consumed by the very next WETH swap with no recovery window.

## Recommendation

Track the ETH provided for the current transaction in transient storage at each payable entry point. Store `msg.value` in a transient slot on entry; in `pay()`, read only that transient amount as available native ETH and clear it after use. This scopes ETH availability to the transaction that provided it.

Alternatively, revert at the start of any payable entry point if `address(this).balance > msg.value`, ensuring no stale ETH can influence the current call.

## Proof of Concept

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

Step 2 — User B's swap consumes stranded ETH:
  UserB calls exactInputSingle(tokenIn=WETH, amountIn=1500, ...)  // no ETH sent
  → pool callback fires, pay(WETH, UserB, pool, 1500) called
  → nativeBalance = 1000 > 0, nativeBalance < 1500
  → deposits router's 1000 ETH as WETH → transfers 1000 WETH to pool
  → safeTransferFrom(UserB, pool, 500)  // pulls only 500 WETH from UserB

Result:
  UserA loses 1000 ETH permanently (unrecoverable).
  UserB pays only 500 WETH instead of 1500 WETH.
  Pool receives correct 1500 WETH total — no pool-level anomaly to detect.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-92)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-130)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-154)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
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
