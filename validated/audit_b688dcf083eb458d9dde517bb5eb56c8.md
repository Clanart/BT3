Audit Report

## Title
Unguarded `refundETH()` allows any caller to steal leftover ETH from users who overpay `exactInputSingle` without an atomic refund step — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
`PeripheryPayments.refundETH()` transfers the router's entire native ETH balance to `msg.sender` with no access control. When a user calls `exactInputSingle{value: V}` with `tokenIn = WETH` and `amountIn < V`, the `pay()` helper wraps only `amountIn` ETH and leaves `V - amountIn` stranded in the router. Any attacker who calls `refundETH()` in a subsequent transaction receives that surplus ETH.

## Finding Description
`refundETH()` is `external payable` with no caller restriction: [1](#0-0) 

When `exactInputSingle` is called with `tokenIn = WETH` and `msg.value > amountIn`, the ETH enters the router because the function is `payable`: [2](#0-1) 

The pool's swap callback triggers `_justPayCallback`, which calls `pay()` with the exact swap-owed amount (not `msg.value`): [3](#0-2) 

Inside `pay()`, when `token == WETH` and `nativeBalance >= value`, only `value` ETH is wrapped and forwarded — the surplus remains in the router: [4](#0-3) 

The `receive()` guard only blocks plain ETH transfers from non-WETH addresses; it does not prevent ETH from entering via payable function calls: [5](#0-4) 

After `exactInputSingle` returns, the surplus `msg.value - amountIn` ETH sits in the router. Any address can then call `refundETH()` and drain it entirely: [1](#0-0) 

## Impact Explanation
Direct theft of user principal. The victim permanently loses `msg.value - amountIn` ETH. This is a **High** severity direct loss of user funds: the attacker receives real ETH that belonged to the user, with no protocol mechanism to recover it. The corrupted value is `address(router).balance` after a standalone `exactInputSingle` call with excess ETH.

## Likelihood Explanation
**Medium-High.** The attack requires no special privileges — only a call to a public function. The surplus ETH window exists between the user's swap transaction and the next block. MEV bots routinely monitor for exactly this pattern. Any user who calls `exactInputSingle` directly (rather than via `multicall` with an appended `refundETH`) with `msg.value > amountIn` and `tokenIn = WETH` is at risk.

## Recommendation
One of:
1. **Auto-refund surplus ETH** at the end of each payable swap entry point — after the swap, if `address(this).balance > 0`, transfer it back to `msg.sender` unconditionally.
2. **Restrict `refundETH` to a stored recipient** — record `msg.sender` at the start of each top-level payable entry point and only allow refund to that address.
3. **Remove standalone `payable` on swap functions** and require all ETH-input flows to go through `multicall`, enforcing that `refundETH` is always the last call in the batch.

## Proof of Concept
```solidity
// 1. User calls exactInputSingle directly with excess ETH
router.exactInputSingle{value: 1 ether}(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 0.5 ether,   // only 0.5 ETH is wrapped and sent to pool
        amountOutMinimum: 0,
        recipient: user,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// router now holds 0.5 ETH (the surplus)

// 2. Attacker calls refundETH() in a separate tx
vm.prank(attacker);
router.refundETH();

// 3. Attacker receives 0.5 ETH; user's surplus is gone
assertEq(attacker.balance, 0.5 ether);
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L75-77)
```text
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
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
