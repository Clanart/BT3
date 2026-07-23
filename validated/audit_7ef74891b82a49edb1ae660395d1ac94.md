Audit Report

## Title
Router Consumes Stranded ETH From Prior Callers During WETH Payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` reads `address(this).balance` as the available native ETH when settling a WETH-input swap. Because `address(this).balance` is persistent contract state rather than per-transaction state, any ETH left in the router from a prior caller who omitted `refundETH` is silently consumed to subsidize the next user's WETH payment. The prior caller permanently loses their ETH with no revert or event.

## Finding Description
In `PeripheryPayments.pay()` (lines 73–84), when `token == WETH`, the function reads `address(this).balance` unconditionally:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
} else if (nativeBalance > 0) {
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
}
``` [1](#0-0) 

`address(this).balance` is not scoped to the current transaction. ETH accumulates in the router whenever a caller sends `msg.value` exceeding `amountIn` on a payable entry point and omits `refundETH`. The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does not prevent ETH accumulation via `msg.value` on payable calls: [2](#0-1) 

`exactInputSingle` and `exactOutputSingle` are both `payable` with no enforcement that `msg.value == amountIn` when `tokenIn == WETH`: [3](#0-2) [4](#0-3) 

`refundETH` is opt-in and not enforced at any entry point: [5](#0-4) 

The swap callback invokes `pay` with the payer set to `msg.sender` (the original caller), so the stranded ETH from a prior caller is spent on behalf of a completely different user's swap: [6](#0-5) 

## Impact Explanation
Direct loss of user ETH principal. The stranded ETH is deposited as WETH and transferred to the pool on behalf of a different user's swap. The original depositor cannot recover it after it has been consumed. This meets the "Critical/High direct loss of user principal" threshold: the amount lost equals the excess ETH sent by the prior caller, which can be arbitrarily large.

## Likelihood Explanation
Any unprivileged trader who calls `exactInputSingle{value: X}(amountIn: Y)` with `X > Y` (a natural mistake, e.g., sending a round-number ETH amount for a swap that costs less) leaves `X - Y` ETH stranded. The stranded ETH is consumed silently by the very next WETH-input swap from any address in any subsequent transaction. A malicious actor can monitor the mempool or router balance and immediately issue a WETH swap to drain the stranded ETH, paying less WETH than owed. No special privileges are required.

## Recommendation
Track the ETH provided for the current transaction using transient storage at each payable entry point. Store `msg.value` in a transient slot on entry to `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`. In `pay`, read only that transient amount as the available native ETH and clear it after use. This scopes ETH availability to the transaction that provided it.

Alternatively, add a check at each payable entry point that reverts if `address(this).balance > msg.value` when `tokenIn == WETH`, ensuring no stale ETH can influence the current call.

## Proof of Concept
```
Setup:
  - Router deployed with WETH address.
  - Pool seeded with WETH/Token1 liquidity.
  - UserA has 2000 ETH. UserB has 1500 WETH approved to router.

Step 1 — UserA strands ETH:
  UserA calls exactInputSingle{value: 2000}(tokenIn=WETH, amountIn=1000, ...)
  → pay(WETH, UserA, pool, 1000): nativeBalance=2000 >= value=1000
  → deposits 1000 ETH as WETH, sends to pool ✓
  → 1000 ETH remains in router (UserA omitted refundETH)

Step 2 — UserB (or attacker) drains stranded ETH:
  UserB calls exactInputSingle(tokenIn=WETH, amountIn=1500, ...) // no ETH sent
  → pay(WETH, UserB, pool, 1500): nativeBalance=1000 > 0, nativeBalance < 1500
  → deposits router's 1000 ETH as WETH → transfers 1000 WETH to pool
  → safeTransferFrom(UserB, pool, 500) // pulls only 500 WETH from UserB

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
