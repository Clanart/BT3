Audit Report

## Title
`refundETH()` Has No Access Control — Any Caller Can Drain ETH Left on the Router by Other Users - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary

`refundETH()` unconditionally transfers the router's entire native ETH balance to `msg.sender` with no per-user accounting or caller restriction. ETH can accumulate on the router between transactions when a user sends excess `msg.value` to a payable swap function and the `pay()` helper consumes only part of it for WETH deposit. Any third party can then call `refundETH()` in a subsequent transaction and steal that ETH.

## Finding Description

`refundETH()` sends the full contract balance to the caller: [1](#0-0) 

No check exists that `msg.sender` is the original depositor, and no per-user balance is tracked.

ETH accumulates via `pay()`. When `token == WETH` and `nativeBalance >= value`, exactly `value` wei is deposited into WETH and the remainder stays as raw ETH on the contract: [2](#0-1) 

All four swap entry points are `payable` and route through `pay()` internally: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

The `receive()` guard only blocks direct ETH sends from non-WETH addresses; it does not prevent ETH from being left on the contract via payable swap calls: [7](#0-6) 

The intended safe pattern is `multicall([swap(...), refundETH()])` in one atomic transaction: [8](#0-7) 

However, this pattern is not enforced. If a user calls a swap function directly (not via `multicall`), or constructs a `multicall` without appending `refundETH()`, the excess ETH persists on the router after the transaction ends and is immediately claimable by any caller.

## Impact Explanation

Direct loss of user principal (native ETH). The attacker receives ETH that belongs to the original sender. No privileged role is required. This meets the Critical/High threshold for direct loss of user funds.

## Likelihood Explanation

Users swapping with WETH as input commonly send a rounded-up `msg.value` and rely on `refundETH()` to recover the excess. Any user who calls a swap function directly without `multicall`, or who omits `refundETH()` from their `multicall`, leaves ETH on the router. An attacker only needs to monitor the router's ETH balance and call `refundETH()` in a subsequent transaction. The condition is observable on-chain and repeatable.

## Recommendation

Track per-sender ETH deposits in transient storage (EIP-1153) and only refund the recorded amount to the original sender, or restrict `refundETH()` so it can only be called as part of a `multicall` initiated by the same `msg.sender` who deposited the ETH.

## Proof of Concept

```solidity
// User A calls exactInputSingle with 1 ETH; swap consumes 0.5 ETH.
// pay() deposits 0.5 ETH into WETH; 0.5 ETH remains on the router.
// User A does NOT append refundETH() — transaction ends.

vm.prank(userA);
router.exactInputSingle{value: 1 ether}(paramsFor0point5ETH);
assertEq(address(router).balance, 0.5 ether);

// Attacker calls refundETH() in a separate transaction.
vm.prank(attacker);
router.refundETH();

assertEq(attacker.balance, 0.5 ether);  // attacker stole userA's ETH
assertEq(address(router).balance, 0);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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
