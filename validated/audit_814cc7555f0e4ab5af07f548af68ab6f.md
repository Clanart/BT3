Audit Report

## Title
Unauthenticated `refundETH()` allows any caller to steal stranded ETH left by payable swap/liquidity entry points — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
Every payable entry point on `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` can leave a native-ETH residue on the router after execution because `pay()` deposits only the exact swap/liquidity amount rather than the full `msg.value`. The `refundETH()` function that is meant to recover this residue has no access control and transfers the router's entire ETH balance to whoever calls it, allowing any third party to steal a victim's stranded ETH.

## Finding Description
In `PeripheryPayments.pay()`, when `token == WETH` and `nativeBalance >= value`, exactly `value` wei is deposited and forwarded to the pool, leaving `nativeBalance − value` on the router:

```solidity
// PeripheryPayments.sol L73-77
} else if (token == WETH) {
  uint256 nativeBalance = address(this).balance;
  if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();   // only `value`, not nativeBalance
    IERC20(WETH).safeTransfer(recipient, value);
```

This is the normal path for exact-output swaps where `msg.value` (= `amountInMaximum`) exceeds the actual pool-required input. None of the payable entry points call `refundETH()` after execution — `exactOutputSingle` (L130-147), `exactOutput` (L154-188), `exactInputSingle` (L67-86), `exactInput` (L92-125), and both overloads of `addLiquidityExactShares` and `addLiquidityWeighted` all return without issuing a refund.

`refundETH()` is entirely unauthenticated:

```solidity
// PeripheryPayments.sol L58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);   // no payer check
    }
  }
```

There is no mapping of depositor → amount, no `msg.sender == original payer` check, and no per-transaction attribution. The design intent — confirmed by the test suite — is that callers compose `multicall{value}([swap, refundETH()])` so the refund is atomic. However, every payable entry point is individually callable without multicall, and a user who calls `exactOutputSingle{value: X}` directly strands `X − amountIn` ETH on the router with no automatic recovery.

## Impact Explanation
Direct loss of user-principal ETH. The stranded amount equals `msg.value − actual_amountIn`, which for exact-output swaps can be a large fraction of `amountInMaximum`. The attacker's cost is a single cheap `refundETH()` call with no special privilege required. This constitutes a Critical/High direct loss of user principal above Sherlock thresholds.

## Likelihood Explanation
The exact-output swap pattern (`exactOutputSingle`, `exactOutput`) is the primary case where a user legitimately cannot know the exact input in advance and will naturally send a conservative `msg.value`. Integrators that wrap the router without multicall (aggregators, smart-contract wallets, scripts) are the most likely victims. A front-running bot watching the mempool can reliably extract the residue in the very next block after the victim's transaction confirms.

## Recommendation
Add an unconditional `refundETH()` call at the end of every payable entry point that may leave a native-ETH residue:

```solidity
function exactOutputSingle(ExactOutputSingleParams calldata params)
    external payable returns (uint256 amountIn)
{
    // ... existing swap logic ...
    refundETH();   // add this
}
```

Apply the same fix to `exactOutput`, `exactInputSingle`, `exactInput`, `addLiquidityExactShares`, and `addLiquidityWeighted`. Alternatively, record the payer address in transient storage at entry and enforce `msg.sender == payer` inside `refundETH()`.

## Proof of Concept
1. Alice calls `router.exactOutputSingle{value: 2 ether}(params)` where `params.amountOut = 1_000` tokens and `params.amountInMaximum = 2 ether`. The pool fills the order for `1 ether` of WETH input.
2. Inside `metricOmmSwapCallback`, `pay()` is called with `value = 1 ether`. Because `address(this).balance (2 ether) >= value (1 ether)`, it deposits exactly `1 ether` as WETH and transfers it to the pool. The remaining `1 ether` stays on the router.
3. Alice's transaction completes. She received her tokens but `1 ether` is stranded on the router — `exactOutputSingle` returns at L146 without calling `refundETH()`.
4. Bob calls `router.refundETH()` in the next transaction. `refundETH()` reads `address(this).balance == 1 ether` and calls `_transferETH(msg.sender, 1 ether)`, sending Bob `1 ether`.
5. Alice has permanently lost `1 ether`.

Foundry test plan: fork the existing `test_multicall_ethInput_exactInputSingle_refundsUnusedEth` test, replace the multicall with a direct `exactOutputSingle{value: 2 ether}` call, then have a second address call `refundETH()` and assert it receives the residual ETH while Alice's balance is short. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
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
