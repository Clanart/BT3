Audit Report

## Title
Stranded ETH from Payable Non-WETH Swaps Silently Consumed by Subsequent WETH-Input Swaps — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

Every swap entry-point on `MetricOmmSimpleRouter` is `external payable`, so ETH sent alongside a non-WETH swap is silently accepted and left in the router. The `pay` function in `PeripheryPayments` reads `address(this).balance` before pulling WETH from the caller, so any stranded ETH is deposited as WETH and forwarded to the pool on behalf of the next WETH-input swap. The original sender's ETH is permanently destroyed with no recourse.

## Finding Description

`receive()` rejects plain ETH transfers from non-WETH addresses: [1](#0-0) 

However, `receive()` is **not** invoked when ETH arrives as `msg.value` to a named `payable` function. All four swap entry-points and `multicall` are declared `external payable`: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

When `tokenIn` is not WETH, `pay` takes the `else` branch and calls `safeTransferFrom` — the ETH is never touched and remains in the router: [7](#0-6) 

When the next user calls any swap with `tokenIn = WETH`, the pool callback invokes `pay(WETH, payer, pool, value)`. The function reads `address(this).balance` first and, if non-zero, uses the router's ETH balance to cover the payment — partially or fully — before touching the caller's WETH allowance: [8](#0-7) 

There is no per-transaction accounting of which ETH belongs to which caller. Any ETH stranded from a prior transaction is indistinguishably pooled with the current `msg.value` and consumed.

## Impact Explanation

Direct, permanent loss of user principal. User A's ETH (sent alongside a non-WETH swap) is silently consumed to fund User B's WETH-input swap. User B receives the full swap output while paying zero WETH from their own balance. This is a concrete loss of funds for an unprivileged user, meeting the Critical/High threshold under Sherlock rules. The attack requires no privileged access, no malicious pool, and no non-standard token behavior.

## Likelihood Explanation

All swap functions are `payable`, so any wallet or front-end can silently attach ETH to a non-WETH swap call. Omitting `refundETH()` in a `multicall` batch is a realistic and common user error. An on-chain attacker can monitor the mempool for transactions that strand ETH in the router and immediately follow with a WETH-input swap of the exact stranded amount. The attack is permissionless, repeatable, and requires no special conditions.

## Recommendation

Remove the native-balance shortcut from `pay` entirely. Require callers who want to pay with ETH to explicitly wrap it first (e.g., via `multicall([wrapETH, swap, refundETH])`). The safe version of `pay`:

```solidity
function pay(address token, address payer, address recipient, uint256 value) internal {
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);
    } else {
        IERC20(token).safeTransferFrom(payer, recipient, value);
    }
}
```

Alternatively, record `msg.value` in transient storage at each swap entry-point and cap `pay`'s native-balance consumption to that per-transaction amount, preventing cross-transaction ETH leakage.

## Proof of Concept

```solidity
// 1. User A sends ETH with a non-WETH swap (e.g., accidentally or via a UI bug)
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    tokenIn: address(token1),  // NOT WETH — pay() takes else branch, ETH untouched
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
// pay() at L74-77: nativeBalance (1 ETH) >= value (1 ETH)
// deposits User A's ETH as WETH, transfers to pool
// weth.balanceOf(userB) == wethBefore  (User B paid nothing)
// User A's 1 ETH is permanently gone
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
