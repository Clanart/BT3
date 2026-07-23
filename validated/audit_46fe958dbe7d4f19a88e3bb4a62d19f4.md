Audit Report

## Title
Leftover Router ETH from Prior Multicall Consumed by Subsequent Caller's WETH-Input Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay()` uses `address(this).balance` — the router's total native ETH balance — as a funding source for any WETH-input swap, with no per-user or per-transaction accounting. ETH sent via `multicall{value: X}` that is not consumed by the swap and not reclaimed via `refundETH()` persists on the router across transactions. A subsequent caller's WETH-input swap silently drains that leftover ETH, causing direct principal loss for the first user and a free or discounted swap for the second.

## Finding Description

In `PeripheryPayments.sol`, the `pay()` function's WETH branch reads `address(this).balance` at L74 and uses it preferentially before pulling from `payer`'s WETH allowance: [1](#0-0) 

There is no mechanism tying this balance to the current transaction's `msg.value` or to any specific user. ETH accumulates on the router when a user calls `multicall{value: X}` and the enclosed swap consumes less than `X`, without a trailing `refundETH()` call: [2](#0-1) 

The `receive()` guard only blocks direct ETH transfers from non-WETH senders, not ETH attached to payable function calls like `multicall`: [3](#0-2) 

`refundETH()` is optional and not enforced by any entry point: [4](#0-3) 

The callback path for a WETH-input `exactInputSingle` sets `msg.sender` as payer via `_setNextCallbackContext`, then `_justPayCallback` calls `pay()` with `_getPayer()` returning User B's address — but `pay()` ignores User B's ETH entirely and instead wraps the router's stale balance: [5](#0-4) [6](#0-5) 

## Impact Explanation

Direct loss of user principal. User A sends `multicall{value: 2 ETH}` with a swap consuming only 1 ETH and no `refundETH()`. The remaining 1 ETH stays on the router. User B calls `exactInputSingle(WETH→token, amountIn=1 ETH)`. Inside `pay()`, `nativeBalance = 1 ETH >= value = 1 ETH`, so the router wraps its own (User A's) ETH and sends WETH to the pool. User B's WETH is never pulled via `transferFrom`. User A loses 1 ETH; User B receives a fully-funded swap at zero cost. This meets the Critical/High direct loss of user principal impact gate.

## Likelihood Explanation

Forgetting `refundETH()` is a common integration mistake in the Uniswap v3 multicall pattern, which this router replicates. An attacker can passively monitor the router's ETH balance on-chain and submit a WETH-input swap immediately after any multicall that leaves residual ETH. No privileged access, no malicious pool, and no non-standard tokens are required — only a standard public `exactInputSingle` call.

## Recommendation

Track the ETH available for the current call rather than the router's total balance. Pass `msg.value` (or a per-call ETH budget) into `pay()` and consume only from that amount, reverting or ignoring any router balance that predates the current transaction. Alternatively, enforce that `address(this).balance` at the start of each top-level entry point equals `msg.value` and revert if stale ETH is present.

## Proof of Concept

```solidity
// Step 1: User A leaves ETH on the router
vm.deal(userA, 2 ether);
vm.prank(userA);
bytes[] memory calls = new bytes[](1);
calls[0] = abi.encodeWithSelector(
    router.exactInputSingle.selector,
    ExactInputSingleParams({
        pool: wethTokenPool, tokenIn: address(weth), tokenOut: address(token1),
        zeroForOne: true, amountIn: 1 ether, amountOutMinimum: 0,
        recipient: userA, deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
    })
);
router.multicall{value: 2 ether}(calls); // sends 2 ETH, no refundETH — 1 ETH stranded

// Step 2: User B's WETH-input swap consumes User A's ETH
uint256 userBWethBefore = weth.balanceOf(userB);
vm.prank(userB);
router.exactInputSingle(
    ExactInputSingleParams({
        pool: wethTokenPool, tokenIn: address(weth), tokenOut: address(token1),
        zeroForOne: true, amountIn: 1 ether, amountOutMinimum: 0,
        recipient: userB, deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
    })
);

assertEq(address(router).balance, 0);             // router ETH drained
assertEq(weth.balanceOf(userB), userBWethBefore); // User B paid nothing
// User A's 1 ETH is gone — consumed by User B's swap
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-198)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
```
