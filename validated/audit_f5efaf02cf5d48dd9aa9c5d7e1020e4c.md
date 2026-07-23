The vulnerability claim is real. Let me trace the exact code path.

**`pay()` in `PeripheryPayments.sol` when `tokenIn=WETH` and `msg.value > amountIn`:** [1](#0-0) 

When `nativeBalance (= msg.value = X+dust) >= value (= amountIn = X)`:
- `deposit{value: value}()` wraps exactly `X` ETH
- `safeTransfer(recipient, value)` sends exactly `X` WETH to the pool
- The remaining `dust` ETH stays on the router as **raw ETH**, not WETH

`exactInputSingle` then returns with no automatic refund: [2](#0-1) 

`refundETH()` sends to `msg.sender`, not the original swap sender: [3](#0-2) 

Any third party can call `refundETH()` after the swap and receive the stranded ETH.

**The protocol explicitly knows about this pattern.** The test suite demonstrates the correct usage — bundling `exactInputSingle` + `refundETH()` in a `multicall`: [4](#0-3) 

The test comment at the top of the file also documents the expected pattern: [5](#0-4) 

---

### Title
Excess ETH sent with `exactInputSingle(tokenIn=WETH)` is not automatically refunded and is stealable by any caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
When a user calls `exactInputSingle` with `tokenIn=WETH` and `msg.value > amountIn`, the `pay()` function wraps only `amountIn` worth of ETH and sends it to the pool. The remaining `(msg.value - amountIn)` ETH is left on the router as raw ETH. Since `exactInputSingle` performs no automatic refund, and `refundETH()` sends to `msg.sender` (not the original swap sender), any third party can call `refundETH()` in a subsequent transaction and steal the stranded ETH.

### Finding Description
In `PeripheryPayments.pay()`, when `token == WETH` and `nativeBalance >= value`:

```solidity
IWETH9(WETH).deposit{value: value}();       // wraps exactly amountIn
IERC20(WETH).safeTransfer(recipient, value); // sends amountIn WETH to pool
// msg.value - amountIn ETH remains on router
```

`exactInputSingle` is `payable` and accepts arbitrary `msg.value`, but never calls `refundETH()` before returning. The stranded ETH is then claimable by anyone:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance); // sends to caller, not original swapper
    }
}
```

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) does not protect against this — it only applies to plain ETH transfers, not to ETH sent via payable function calls.

### Impact Explanation
Any ETH sent above `amountIn` in a direct (non-multicall) `exactInputSingle` call is permanently stranded on the router until a third party calls `refundETH()` and claims it. The original sender has no priority claim. The amount at risk equals `msg.value - amountIn` per affected transaction.

### Likelihood Explanation
Users who call `exactInputSingle` directly (not via `multicall`) and send `msg.value > amountIn` — e.g., to cover slippage on the ETH side, or by mistake — are affected. The protocol's own test (`test_multicall_ethInput_exactInputSingle_refundsUnusedEth`) shows the correct pattern requires an explicit `refundETH()` call bundled in a multicall, which is non-obvious to integrators calling the function directly.

### Recommendation
Add an automatic ETH refund at the end of `exactInputSingle` (and `exactInput`) when `tokenIn == WETH`:

```solidity
// After swap completes, refund any unused ETH to msg.sender
if (address(this).balance > 0) {
    _transferETH(msg.sender, address(this).balance);
}
```

Alternatively, document prominently that callers **must** use `multicall([exactInputSingle(...), refundETH()])` when sending ETH, and consider adding a check that reverts if `msg.value > amountIn` when `tokenIn == WETH` to prevent accidental overpayment.

### Proof of Concept
```solidity
// Alice calls exactInputSingle directly with excess ETH
router.exactInputSingle{value: amountIn + 1 ether}(
    ExactInputSingleParams({tokenIn: WETH, amountIn: amountIn, ...})
);
// 1 ether is now stranded on the router

// Bob (attacker) calls refundETH() and receives Alice's 1 ether
router.refundETH(); // Bob receives 1 ether
```

Foundry assertion: after `exactInputSingle{value: amountIn + dust}`, assert `address(router).balance == 0` — this assertion **fails**, confirming the dust is stranded and stealable.

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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L8-10)
```text
/// @dev Native ETH flows follow Uniswap v3-periphery multicall patterns:
///      - ETH input: multicall{value}(exactInput*) with WETH as tokenIn
///      - ETH output: swap WETH to router, then unwrapWETH9 in the same multicall
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
