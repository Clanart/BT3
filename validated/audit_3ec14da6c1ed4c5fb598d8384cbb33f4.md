Audit Report

## Title
Unguarded `refundETH()` allows any caller to steal ETH stranded in the router between transactions — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.refundETH()` has no access control and unconditionally sends the router's entire ETH balance to `msg.sender`. ETH is routinely stranded in the router when users call payable swap functions with `msg.value` exceeding the actual swap cost — a normal pattern for exact-output swaps. Any attacker can call `refundETH()` in a subsequent transaction to steal that stranded ETH.

## Finding Description
`refundETH()` is `external payable` with zero access control:

```solidity
// PeripheryPayments.sol L58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // sends to ANY caller
    }
}
``` [1](#0-0) 

ETH is stranded because `pay()` wraps only the exact `value` required by the pool callback, not the full `nativeBalance`:

```solidity
// PeripheryPayments.sol L75-77
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();   // only `value`, not nativeBalance
    IERC20(WETH).safeTransfer(recipient, value);
}
``` [2](#0-1) 

All swap entry points are `payable`: `exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, and `multicall`. [3](#0-2) [4](#0-3) 

The `receive()` guard does not prevent stranding — it only blocks direct ETH pushes from non-WETH addresses; ETH entering via `msg.value` on payable functions is entirely unaffected: [5](#0-4) 

The intended recovery mechanism is to append `refundETH()` as the last call in a `multicall`. Because `multicall` uses `delegatecall` in a single atomic transaction, no external call can interleave mid-multicall. However, if a user calls `exactOutputSingle` directly (not via multicall), or forgets to append `refundETH()`, the excess ETH is left in the router after the transaction and is immediately claimable by any address in a subsequent transaction. [6](#0-5) 

## Impact Explanation
Direct theft of user ETH. Any ETH stranded in the router after a swap (excess `msg.value`) is immediately claimable by any address. The router is a shared contract; stranded ETH from one user is visible and accessible to all other callers. This constitutes a direct loss of user principal.

## Likelihood Explanation
`exactOutputSingle` and `exactOutput` are the primary affected paths — callers routinely over-provision ETH because the exact input amount is unknown before execution. The test suite itself demonstrates this pattern: `test_multicall_ethInput_exactInputSingle_refundsUnusedEth` sends `2 ether` for a `1_000` unit swap, relying on `refundETH()` within the same multicall to recover the excess. [7](#0-6) 

Any attacker monitoring the mempool can back-run any swap transaction that leaves ETH in the router. The attack requires no special privileges, no capital beyond gas, and is repeatable.

## Recommendation
Restrict `refundETH()` so it can only refund the original transaction initiator. Two viable approaches:

1. **Transient storage tracking**: Record the outermost `msg.sender` in transient storage at the start of each `multicall` (or each payable swap entry point) and require `refundETH()` to only transfer to that stored address.
2. **Caller restriction**: Require `refundETH()` to be called only via `delegatecall` from within a `multicall` (e.g., check `msg.sender == address(this)`), so the refund always goes to the original multicall initiator.

## Proof of Concept
```solidity
// 1. User A calls exactOutputSingle directly with excess ETH (actual cost = 1 ether)
router.exactOutputSingle{value: 2 ether}(ExactOutputSingleParams({
    tokenIn: address(weth),
    amountOut: someAmount,
    amountInMaximum: 2 ether,
    // actual amountIn settled = 1 ether
    ...
}));
// After tx: router holds 1 ether of stranded ETH

// 2. Attacker (separate tx) drains it
vm.prank(attacker);
router.refundETH();
assertEq(attacker.balance, 1 ether); // attacker stole user A's ETH
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-130)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
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
