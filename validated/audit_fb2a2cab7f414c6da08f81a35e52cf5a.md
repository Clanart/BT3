Audit Report

## Title
Stranded ETH from Payable Swap Calls Silently Consumed by Subsequent WETH-Input Swaps — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay` reads `address(this).balance` before pulling WETH from the caller. Any ETH left in the router from a prior payable swap call where `tokenIn` is not WETH is silently deposited as WETH and forwarded to the pool on behalf of the next WETH-input swap. The original ETH depositor suffers a permanent, direct loss of principal with no revert or warning.

## Finding Description

`receive()` rejects plain ETH transfers from non-WETH addresses: [1](#0-0) 

However, `receive()` is **not** invoked when ETH is attached as `msg.value` to a named `payable` function. All swap entry-points are declared `external payable`: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

When `tokenIn` is not WETH, `pay` takes the `else` branch and calls `safeTransferFrom` — the attached ETH is never touched and remains stranded in the router: [7](#0-6) 

When the next user calls any swap with `tokenIn = WETH`, the callback invokes `pay(WETH, payer, pool, value)`. The function reads `address(this).balance` first: [8](#0-7) 

If `nativeBalance >= value`, the entire payment is sourced from the router's ETH balance — the legitimate caller's WETH allowance is never touched. If `0 < nativeBalance < value`, the stranded ETH partially covers the payment. In both cases the original depositor's ETH is permanently consumed with no accounting or revert.

The `_justPayCallback` and `_exactOutputIterateCallback` both route through `pay`, so all four swap variants (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) are affected: [9](#0-8) [10](#0-9) 

## Impact Explanation

**User A (victim):** calls `exactInputSingle{value: 1 ETH}` with a non-WETH `tokenIn`. Their 1 ETH is silently stranded in the router and later consumed — direct, permanent loss of principal with no recourse.

**User B (beneficiary/attacker):** calls `exactInputSingle` with `tokenIn = WETH`, `amountIn = 1 ETH`. The router deposits User A's ETH as WETH and forwards it to the pool. User B pays 0 WETH from their own balance for a 1 ETH swap.

This is a direct loss of user principal meeting Critical/High severity under Sherlock thresholds. The wrong value is `address(this).balance` being consumed from a prior caller's unrelated transaction instead of being sourced exclusively from the current payer.

## Likelihood Explanation

- All swap functions are `payable`, so wallets and front-ends can silently attach ETH to any swap call without triggering `receive()`.
- Omitting `refundETH()` after a non-WETH payable swap is a realistic user error, especially in `multicall` batches.
- An on-chain attacker can monitor the mempool for transactions that strand ETH in the router and immediately follow with a WETH-input swap to drain the balance.
- No privileged access, no malicious pool, no non-standard token required. Any unprivileged trader can trigger both sides of the exploit.

## Recommendation

Remove the native-ETH shortcut from `pay` entirely. Require callers to explicitly wrap ETH via `multicall([wrapETH, swap, refundETH])`. The safe version of `pay`:

```solidity
function pay(address token, address payer, address recipient, uint256 value) internal {
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);
    } else {
        IERC20(token).safeTransferFrom(payer, recipient, value);
    }
}
```

Alternatively, record `msg.value` in transient storage at each swap entry-point and cap the consumable native balance to that amount, so only the current caller's ETH can be used within the same transaction.

## Proof of Concept

```solidity
// 1. User A accidentally sends ETH with a non-WETH swap
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    tokenIn: address(token1),   // NOT WETH — pay() takes else branch, ETH untouched
    tokenOut: address(token2),
    amountIn: 1000,
    ...
}));
// router.balance == 1 ether; User A's ETH is stranded

// 2. User B exploits the stranded ETH
uint256 wethBefore = weth.balanceOf(userB);
router.exactInputSingle(ExactInputSingleParams({
    tokenIn: address(weth),
    amountIn: 1 ether,
    ...
}));
// pay() reads nativeBalance = 1 ETH >= value = 1 ETH
// deposits User A's ETH as WETH, transfers to pool
// weth.balanceOf(userB) == wethBefore (unchanged)
// User A's 1 ETH is permanently gone
```

A Foundry integration test can confirm this by: (1) calling `exactInputSingle{value: 1 ether}` with a non-WETH `tokenIn` from address A, (2) asserting `address(router).balance == 1 ether`, (3) calling `exactInputSingle` with `tokenIn = WETH, amountIn = 1 ether` from address B without any WETH approval, and (4) asserting the swap succeeds and `weth.balanceOf(B)` is unchanged.

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L85-87)
```text
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-39)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L207-213)
```text
    if (tradesLeft == 0) {
      // forge-lint: disable-next-line(unsafe-typecast)
      uint256 amountIn = uint256(amountToPay);
      if (amountIn > cb.amountInMax) revert InputTooHigh(amountIn, cb.amountInMax);
      _setExactOutputAmountIn(amountIn);
      pay(_getTokenToPay(), _getPayer(), msg.sender, amountIn);
      return;
```
