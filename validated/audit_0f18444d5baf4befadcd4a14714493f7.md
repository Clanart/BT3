All cited code is confirmed in the repository. The vulnerability is real and fully traceable:

- `refundETH()` has zero access control and sends `address(this).balance` to `msg.sender` [1](#0-0) 
- `pay()` wraps only `value` ETH when `nativeBalance >= value`, leaving any excess stranded [2](#0-1) 
- `exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, and `multicall` are all `external payable` [3](#0-2) 
- The test suite confirms the stranding pattern (2 ether sent for a 1_000 unit swap) [4](#0-3) 

---

Audit Report

## Title
Unguarded `refundETH()` allows any caller to steal ETH stranded in the router between transactions — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.refundETH()` has no access control and unconditionally transfers the router's entire ETH balance to `msg.sender`. ETH is routinely stranded in the router when users call payable swap functions with `msg.value` exceeding the actual swap cost — a normal pattern for exact-output swaps. Any attacker can call `refundETH()` in a subsequent transaction to steal that stranded ETH.

## Finding Description
`pay()` in `PeripheryPayments.sol` wraps only the exact `value` of ETH required by the pool callback when `token == WETH` and `nativeBalance >= value`:

```solidity
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
}
```

Any `msg.value` above `value` remains in the router after the transaction completes. The intended recovery mechanism is to append `refundETH()` as the last call in a `multicall`. However, `refundETH()` itself is completely unrestricted:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // sends to ANY caller
    }
}
```

An attacker monitoring the mempool can back-run any swap transaction that leaves ETH in the router and call `refundETH()` in a separate transaction to drain it. The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks direct ETH pushes from non-WETH addresses and does not prevent ETH from entering via `msg.value` on payable functions.

## Impact Explanation
Direct theft of user ETH principal. Any ETH stranded in the router after a swap (excess `msg.value`) is immediately claimable by any address in a subsequent transaction. The router is a shared contract; stranded ETH from one user is visible and accessible to all. This constitutes a direct loss of user funds above Sherlock thresholds.

## Likelihood Explanation
`exactOutputSingle` and `exactOutput` are the primary affected paths — callers routinely over-provision ETH because the exact input amount is unknown before execution. The test suite itself demonstrates this pattern: `test_multicall_ethInput_exactInputSingle_refundsUnusedEth` sends `2 ether` for a `1_000` unit swap, relying on `refundETH()` within the same multicall to recover the excess. Any user who forgets to append `refundETH()` to their multicall, or who calls `exactOutputSingle` directly with excess ETH, is immediately vulnerable. The attack requires no special privileges and is repeatable on every such transaction.

## Recommendation
Restrict `refundETH()` so it can only refund the original `msg.sender` of the outermost call. Two viable approaches:
1. Track the original caller in transient storage at the start of each payable entry point (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `multicall`) and require `msg.sender == storedCaller` inside `refundETH()`.
2. Require `msg.sender == address(this)` in `refundETH()` so it can only be invoked via `delegatecall` from within `multicall`, where `msg.sender` is the original caller.

## Proof of Concept
```solidity
// 1. User A calls exactOutputSingle with excess ETH (actual cost = 1 ether)
router.exactOutputSingle{value: 2 ether}(ExactOutputSingleParams({
    tokenIn: address(weth),
    amountOut: someAmount,
    amountInMaximum: 2 ether,
    ...
}));
// After tx: router holds 1 ether of stranded ETH

// 2. Attacker (separate tx) drains it
vm.prank(attacker);
router.refundETH();
assertEq(attacker.balance, 1 ether); // attacker stole user A's ETH
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L75-77)
```text
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
  }
```
