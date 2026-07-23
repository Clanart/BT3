Audit Report

## Title
`refundETH` Drains Any Stranded ETH to Arbitrary Caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`refundETH()` sends the router's entire native ETH balance to `msg.sender` with no per-depositor accounting. When a user overpays ETH on any payable swap entry point, `pay()` wraps only the exact amount owed to the pool, leaving the surplus on the contract. Any address that calls `refundETH()` before the original depositor claims the full residual balance.

## Finding Description

`refundETH()` contains no guard linking the refund to the caller's own deposit — it unconditionally transfers `address(this).balance` to whoever calls it: [1](#0-0) 

`pay()`, when `token == WETH` and `nativeBalance >= value`, wraps exactly `value` wei and leaves any surplus ETH on the contract: [2](#0-1) 

All four swap entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) are `payable`, so users routinely send ETH with these calls: [3](#0-2) 

The `receive()` guard only blocks direct ETH transfers from non-WETH addresses; it does not apply to ETH sent alongside a payable function call, so excess ETH from overpayment is silently retained on the contract after the swap callback returns.

`multicall` uses `delegatecall`, so `msg.sender` inside `refundETH` resolves to the attacker's EOA, not the original depositor: [4](#0-3) 

Exploit flow:
1. User A calls `exactInputSingle{value: 2 ether}(...)` where the pool only consumes 1 ETH.
2. The swap callback triggers `pay()`, which wraps exactly 1 ETH; 1 ETH remains on the router.
3. Attacker calls `refundETH()` (directly or via `multicall`) and receives User A's 1 ETH.
4. User A's subsequent `refundETH()` call finds a zero balance.

No existing guard prevents step 3: there is no per-depositor ledger, no reentrancy lock tying the refund to the originating call, and no access control on `refundETH`.

## Impact Explanation

Direct loss of user ETH principal. Any ETH overpaid by a user (a common pattern for slippage tolerance) is immediately claimable by any address. This meets the Critical/High threshold for direct loss of user principal under the allowed impact gate.

## Likelihood Explanation

ETH-in swaps are a primary router use case. Users routinely send a small ETH surplus to cover slippage. MEV bots can monitor the mempool for payable swap transactions and front-run the victim's own `refundETH` call, or simply call it in a subsequent block if the victim does not immediately reclaim. No special privilege is required — the attacker only needs to call a public, permissionless function.

## Recommendation

Track per-caller ETH deposits in transient storage (EIP-1153) at the start of each payable entry point and restrict `refundETH` to return only the caller's own recorded deposit. Alternatively, enforce that `refundETH` can only be called within the same `multicall` batch as the originating swap by checking a transient "active multicall initiator" slot.

## Proof of Concept

```solidity
// Foundry test sketch
function test_refundETH_steals_excess() public {
    // User A calls exactInputSingle with 2 ETH; swap only consumes 1 ETH
    vm.deal(userA, 2 ether);
    vm.prank(userA);
    router.exactInputSingle{value: 2 ether}(params); // pay() wraps 1 ETH, 1 ETH stranded

    // Attacker calls refundETH and receives userA's 1 ETH
    uint256 before = attacker.balance;
    vm.prank(attacker);
    router.refundETH();
    assertEq(attacker.balance - before, 1 ether); // attacker stole A's ETH
    assertEq(address(router).balance, 0);
}
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```
