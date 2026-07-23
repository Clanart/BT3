Audit Report

## Title
Stranded ETH in Router Consumed by Subsequent User's WETH Payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay` uses `address(this).balance` — the router's total native ETH balance — when settling a WETH-input swap. ETH left in the router by a prior user (who sent ETH via `multicall{value}` for a non-WETH swap and omitted `refundETH`) is silently consumed to cover a later user's WETH obligation, causing direct, permanent ETH loss for the first user.

## Finding Description

`pay` in `PeripheryPayments.sol` has three branches for `token == WETH, payer != address(this)`: [1](#0-0) 

`nativeBalance = address(this).balance` captures the router's **entire** ETH balance with no per-user accounting. Any ETH sitting in the router — regardless of who deposited it — is treated as available to satisfy the current caller's WETH obligation.

ETH accumulates in the router because `multicall` is `payable`: [2](#0-1) 

When a user calls `multicall{value: X}([exactInputSingle(token1→token2)])`, the swap uses `token1`, so `pay` hits the plain `safeTransferFrom` branch at L86. The ETH is never touched and remains in the router.

The `receive()` guard only blocks direct ETH pushes (plain transfers with no calldata); it does not prevent ETH from accumulating via `payable` function entry points like `multicall`: [3](#0-2) 

The callback path that triggers `pay` is: [4](#0-3) 

## Impact Explanation

Direct, permanent loss of user principal (ETH). User A's ETH is consumed to subsidize User B's WETH swap. User B (or a MEV bot) receives the full swap output while paying fewer tokens from their own wallet than the pool actually required. This is a direct fund-loss impact on an unprivileged user with no recovery path.

## Likelihood Explanation

Omitting `refundETH` from a `multicall` is a common user mistake, especially when the swap is for a non-WETH token and the user sends ETH speculatively. A MEV bot can watch for transactions that leave ETH in the router and immediately follow with a WETH swap sized to consume exactly the stranded ETH, requiring fewer tokens from their own wallet. No privileged access, malicious pool, or non-standard token is required.

## Recommendation

Track per-call ETH entitlement in transient storage (set at `multicall` entry, decremented in `pay`) and only allow `pay` to consume ETH up to the current call's entitlement. Alternatively, auto-refund any remaining ETH at the end of each `multicall` iteration, or reject ETH sent to `multicall` when no WETH-input swap is present in the batch.

## Proof of Concept

```solidity
// 1. User A strands 1 ETH in the router
vm.prank(userA);
bytes[] memory calls = new bytes[](1); // no refundETH
calls[0] = abi.encodeCall(router.exactInputSingle, ExactInputSingleParams({
    tokenIn: address(token1), tokenOut: address(token2), ...
}));
router.multicall{value: 1 ether}(calls);
assertEq(address(router).balance, 1 ether); // stranded

// 2. User B swaps WETH→token2 with amountIn=2 ether, only 1 WETH approved
weth.approve(address(router), 1 ether); // only 1 WETH
vm.prank(userB);
router.exactInputSingle(ExactInputSingleParams({
    tokenIn: address(weth), tokenOut: address(token2),
    amountIn: 2 ether, ...
}));

// 3. Assertions
assertEq(address(router).balance, 0);            // user A's ETH consumed
assertEq(weth.balanceOf(userB), initialWeth - 1 ether); // userB paid only 1 WETH
// userA's ETH balance permanently reduced by 1 ether with no benefit
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
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
