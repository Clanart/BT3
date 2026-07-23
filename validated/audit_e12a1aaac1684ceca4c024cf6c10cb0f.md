Audit Report

## Title
Unrestricted `sweepToken`, `unwrapWETH9`, and `refundETH` Allow Any Caller to Drain Router-Held Balances - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
`sweepToken` and `unwrapWETH9` are `public payable` with no `msg.sender` restriction and accept a fully caller-controlled `recipient` address. `refundETH` is similarly unrestricted and sends the entire router ETH balance to any caller. The router's intended multicall usage pattern (swap + refund/unwrap atomically) means funds legitimately land on the router mid-transaction, but a direct (non-multicall) call strands those funds permanently until any third party drains them.

## Finding Description
`sweepToken` and `unwrapWETH9` impose no check on `msg.sender` and forward the entire router balance to a caller-supplied `recipient`: [1](#0-0) 

`refundETH` sends the entire router ETH balance to `msg.sender` with no restriction: [2](#0-1) 

The `pay()` internal function consumes only `value` wei from `address(this).balance` when `token == WETH`, leaving any excess `msg.value` silently stranded on the router: [3](#0-2) 

The intended usage pattern (confirmed by tests and the comment in `MetricOmmSimpleRouterNativeTest`) is to atomically combine a swap with `refundETH`/`unwrapWETH9` inside a single `multicall`: [4](#0-3) 

However, `exactInputSingle` is independently `payable` and callable without `multicall`: [5](#0-4) 

When a user calls `exactInputSingle` directly with `tokenIn=WETH` and `msg.value > amountIn`, the excess ETH is stranded on the router with no attribution. Similarly, when a user calls any swap with `recipient=address(router)` (the required pattern for the WETH-output flow), the output tokens land on the router. In both cases, any unprivileged caller can immediately drain the balance by calling `refundETH()`, `sweepToken(token, 0, attacker)`, or `unwrapWETH9(0, attacker)`.

No existing guard prevents this: there is no per-depositor accounting, no `msg.sender == depositor` check, and no reentrancy lock that would prevent a third-party call between the swap transaction and the user's intended refund call.

## Impact Explanation
Direct loss of user principal. Any ETH or ERC-20 balance stranded on the router — from excess `msg.value` on a WETH swap, from a swap with `recipient: address(router)`, or from any other source — can be immediately stolen by an unprivileged attacker. The loss is bounded only by the router balance at the time of the attack, which can be arbitrarily large. This meets the Sherlock Critical/High threshold for direct loss of user principal with no privilege required.

## Likelihood Explanation
The router is `payable` on every swap entry point. A user who calls `exactInputSingle` directly (not via `multicall`) with `msg.value > amountIn`, or who uses the WETH-output pattern (`recipient: address(router)`) without atomically chaining `unwrapWETH9`, will strand funds. A MEV bot monitoring the mempool can front-run the user's intended `refundETH` call or simply call `sweepToken`/`unwrapWETH9` in the next block. No special privilege, allowance, or prior interaction is required. The attack is repeatable on every such transaction.

## Recommendation
Remove the caller-controlled `recipient` parameter from `sweepToken` and `unwrapWETH9` and always transfer to `msg.sender`, matching the behavior of `refundETH`. This eliminates the ability to redirect funds to an arbitrary address. Optionally, add per-depositor accounting in transient storage to restrict `refundETH`, `sweepToken`, and `unwrapWETH9` to only recover balances attributable to `msg.sender`.

## Proof of Concept

```solidity
// Step 1 – User calls exactInputSingle directly (no multicall) with excess msg.value
vm.prank(userA);
router.exactInputSingle{value: 2_000}(ExactInputSingleParams({
    pool: pool,
    tokenIn: address(weth),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1_000,          // only 1_000 consumed by pay()
    amountOutMinimum: 0,
    recipient: userA,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// router.balance == 1_000 (stranded, no attribution)

// Step 2 – Attacker calls refundETH() and receives userA's 1_000 ETH
vm.prank(attacker);
router.refundETH();
assertEq(attacker.balance, 1_000);

// Alternative: if WETH output was sent to the router
// vm.prank(attacker);
// router.sweepToken(address(weth), 0, attacker);
// assertEq(weth.balanceOf(attacker), routerWethBalance);
```

### Citations

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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L8-10)
```text
/// @dev Native ETH flows follow Uniswap v3-periphery multicall patterns:
///      - ETH input: multicall{value}(exactInput*) with WETH as tokenIn
///      - ETH output: swap WETH to router, then unwrapWETH9 in the same multicall
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
