The core of this question is whether `refundETH()` can be called by an unprivileged third party to steal ETH that a victim left on the router. Let me verify the exact implementation.

The implementation is fully visible. Here is the analysis:

---

**Core facts from the code:**

`refundETH()` is `external payable` with zero access control — it unconditionally sends the entire native ETH balance of the router to `msg.sender`: [1](#0-0) 

The `receive()` guard blocks direct ETH pushes from non-WETH addresses, but it does **not** block ETH arriving via `msg.value` in any `payable` function: [2](#0-1) 

The `pay()` function, when `token == WETH` and `nativeBalance >= value`, wraps only `value` wei and leaves the rest on the contract: [3](#0-2) 

`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, and `multicall` are all `payable` and perform no automatic ETH refund: [4](#0-3) 

---

**Attack path analysis:**

The question's "front-run within the victim's multicall" framing is incorrect — `multicall` is atomic, so no interleaving is possible. The real attack is a **subsequent transaction**:

1. Victim calls `exactInputSingle{value: 2 ether}(amountIn: 1000, tokenIn: WETH, ...)` directly (or via `multicall` without appending `refundETH`).
2. `pay()` wraps 1000 wei of ETH; `~2 ether − 1000 wei` remains on the router after the transaction settles.
3. Attacker calls `refundETH()` in a separate transaction; the entire router ETH balance is sent to the attacker.

This is a real, direct loss of user funds. No privileged role, no malicious pool, no non-standard token is required.

---

**Why the invariant is broken:**

The design intent (evidenced by the test `test_multicall_ethInput_exactInputSingle_refundsUnusedEth`) is that users always bundle `refundETH()` as the last call in their `multicall`. The contract does not enforce this. Any user who calls a `payable` swap function directly with excess ETH, or who omits `refundETH` from their multicall, leaves ETH on the router that any third party can immediately claim. [5](#0-4) 

---

### Title
Unprivileged caller can steal stranded ETH via `PeripheryPayments.refundETH` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`refundETH()` has no access control and sends the router's entire ETH balance to `msg.sender`. ETH accumulates on the router whenever a user calls any `payable` swap function with `msg.value` exceeding the actual swap cost and does not include `refundETH` in the same atomic `multicall`. A third party can then drain that ETH.

### Finding Description
`PeripheryPayments.refundETH()` is declared `external payable` with no `msg.sender` check. It transfers `address(this).balance` to whoever calls it. The `receive()` guard only blocks direct ETH pushes; ETH arriving via `msg.value` in `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, or `multicall` bypasses it entirely. Inside `pay()`, when `nativeBalance >= value`, only `value` wei is consumed; the remainder stays on the contract. If the user does not append `refundETH()` to the same `multicall`, that remainder is claimable by any address in a subsequent transaction.

### Impact Explanation
Direct loss of user ETH. The attacker receives ETH that belongs to the victim. Loss magnitude equals `msg.value − amountIn` for the victim's transaction, which can be arbitrarily large.

### Likelihood Explanation
Moderate. `exactInputSingle` and the other swap functions are `payable` and callable directly (not only via `multicall`). Users who call them directly with excess ETH, or who construct a `multicall` without a trailing `refundETH`, are immediately vulnerable. Attackers need only monitor the mempool or the router's ETH balance.

### Recommendation
Either (a) record the original `msg.sender` in transient storage at `multicall` entry and restrict `refundETH` to that stored address, or (b) automatically refund `address(this).balance − msg.value_consumed` to `msg.sender` at the end of each top-level swap function, eliminating the need for a separate `refundETH` call.

### Proof of Concept
```solidity
// Foundry fork test sketch
function test_attacker_steals_stranded_eth() public {
    address victim  = makeAddr("victim");
    address attacker = makeAddr("attacker");
    vm.deal(victim, 2 ether);

    // Victim swaps 1000 wei of WETH but sends 2 ether, omitting refundETH
    vm.prank(victim);
    router.exactInputSingle{value: 2 ether}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            recipient: victim,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // ~2 ether - 1000 wei is now stranded on the router

    uint256 attackerBefore = attacker.balance;
    vm.prank(attacker);
    router.refundETH();  // no access control — succeeds

    assertGt(attacker.balance, attackerBefore, "attacker stole victim ETH");
    assertEq(address(router).balance, 0);
    // victim's ETH is gone
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-77)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
