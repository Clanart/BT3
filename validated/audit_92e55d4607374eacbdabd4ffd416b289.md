Audit Report

## Title
Unattributed Router ETH Balance Enables Theft via `refundETH` and Free Swaps via `pay` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay` function consumes `address(this).balance` without verifying the ETH belongs to the current `payer`, and `refundETH()` is an unrestricted external function that sends the router's entire ETH balance to any caller. When a user calls `exactOutputSingle{value: X}` directly and the pool requires only `Y < X`, the excess `X - Y` ETH is stranded on the router and immediately claimable by any third party via `refundETH()`, or silently consumed to fund a subsequent attacker's swap via `pay`.

## Finding Description

**ETH stranding path:** `exactOutputSingle` is `external payable` and accepts `msg.value` directly. [1](#0-0) 
The pool determines the actual `amountIn` only after execution. A user who sends a conservative overage (e.g., `msg.value = 1 ETH`, pool requires `0.6 ETH`) will have the excess `0.4 ETH` remain on the router after the call returns. The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) does not prevent this — it only blocks bare ETH transfers with no calldata; ETH arriving as `msg.value` in a payable function call bypasses `receive()` entirely. [2](#0-1) 

**Root cause — `pay` uses unattributed balance:** When `token == WETH`, `pay` reads `address(this).balance` and uses it unconditionally to cover the current swap, with no check that the ETH was deposited by the current `payer`. [3](#0-2) 

**Root cause — `refundETH` has no access control:** The function is `external payable` and transfers the router's entire ETH balance to `msg.sender` unconditionally. [4](#0-3) 

**Exploit path 1 — ETH theft via `refundETH`:**
1. User A calls `exactOutputSingle{value: 1 ether}(...)`. Pool requires `0.6 ETH`; `pay` wraps and sends `0.6 ETH` to the pool. `0.4 ETH` remains on the router.
2. Attacker calls `router.refundETH()`. No guard exists; attacker receives `0.4 ETH`. User A's funds are permanently lost.

**Exploit path 2 — Free swap via `pay` consuming stranded ETH:**
1. `0.4 ETH` is stranded on the router (from step 1 above, or any prior transaction).
2. Attacker calls `exactInputSingle{value: 0}(tokenIn=WETH, amountIn=0.4 ether, ...)`.
3. `pay` is invoked with `payer = attacker`, `token = WETH`, `value = 0.4 ETH`. It reads `nativeBalance = 0.4 ETH >= value`, wraps the stranded ETH, and sends it to the pool.
4. Attacker receives swap output without spending any ETH or WETH of their own.

The test suite confirms `exactOutputSingle{value: nativePart}` is called directly without a paired `refundETH`, establishing this as a supported usage pattern. [5](#0-4) 

`sweepToken` and `unwrapWETH9` are equally unguarded — both are `public payable` and transfer the router's full token/WETH balance to a caller-supplied `recipient` with no attribution check. [6](#0-5) 

## Impact Explanation
Direct, unrecoverable loss of user principal (native ETH). Any ETH stranded on the router from an `exactOutputSingle` overage is immediately claimable by an unprivileged attacker via `refundETH`, or silently consumed to fund a free swap via `pay`. This meets the Sherlock critical/high threshold for direct loss of user funds with no preconditions beyond the victim's own transaction.

## Likelihood Explanation
`exactOutputSingle` is the primary high-risk entry point: users cannot know the exact input amount before execution, making a conservative `msg.value` overage the natural usage pattern. The test at `MetricOmmSimpleRouter.native.t.sol:84` demonstrates calling `exactOutputSingle{value: nativePart}` directly without multicall, confirming this is an expected and supported path. MEV bots routinely monitor contract ETH balances and can atomically steal stranded ETH in the same block. No special privileges, approvals, or setup are required by the attacker.

## Recommendation
1. **Track per-caller ETH deposits in transient storage.** When `msg.value > 0` is received in a payable swap function, record `msg.sender → msg.value` in a transient slot. In `refundETH`, only return the amount attributed to `msg.sender`, not `address(this).balance`.
2. **Restrict `pay` to attributed ETH only.** When `token == WETH`, compare `msg.value` (or the transient per-caller deposit) against `value` rather than `address(this).balance`, so only the current caller's deposited ETH is consumed.
3. **Apply the same guard to `sweepToken` and `unwrapWETH9`.** Restrict these to `multicall`-only contexts or require transient attribution before transferring balances.

## Proof of Concept

```solidity
// 1. User A strands ETH
router.exactOutputSingle{value: 1 ether}(ExactOutputSingleParams({
    tokenIn: WETH,
    amountOut: someTokenAmount,   // pool requires 0.6 ETH input
    amountInMaximum: 1 ether,
    ...
}));
// 0.4 ETH now stranded on router

// 2a. Attacker steals via refundETH (no access control)
router.refundETH();  // attacker receives 0.4 ETH

// 2b. OR attacker gets a free swap via pay consuming stranded ETH
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    tokenIn: WETH,
    amountIn: 0.4 ether,   // <= stranded balance
    ...
}));
// pay() enters nativeBalance >= value branch, wraps User A's 0.4 ETH
// attacker receives swap output at zero cost
```

A Foundry test can reproduce this by: (1) calling `exactOutputSingle{value: quotedIn * 2}` to strand the overage, (2) pranking a second address to call `refundETH()`, and (3) asserting the second address received the stranded ETH while the original swapper's balance is short.

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-130)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-55)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }

  /// @inheritdoc IPeripheryPayments
  function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);

    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
    }
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L83-103)
```text
    vm.prank(swapper);
    uint256 amountIn = router.exactOutputSingle{value: nativePart}(
      IMetricOmmSimpleRouter.ExactOutputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountOut: amountOut,
        amountInMaximum: uint128(quotedIn * 2 + 1),
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );

    assertEq(amountIn, quotedIn, "amountIn matches quote");
    assertEq(token1.balanceOf(recipient) - token1Before, amountOut, "exact token1 out");
    assertEq(swapperEthBefore - swapper.balance, nativePart, "swapper native spent");
    assertEq(swapperWethBefore - weth.balanceOf(swapper), wethPart, "swapper weth spent");
    _assertRouterEmpty();
```
