The `pay` function in `PeripheryPayments.sol` is the critical code to analyze. I have it fully. Let me now verify the exact branch logic and the router's ETH-stranding surface.

### Title
Stranded Router ETH Subsidizes Attacker WETH Swap via Partial-ETH Branch in `pay()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` function in `PeripheryPayments` contains a partial-ETH branch that silently consumes **all** native ETH held by the router when `0 < router.balance < value`. Any unprivileged caller can exploit this to drain ETH that was stranded in the router by a prior user, receiving a subsidized swap while only having their WETH approval pulled for the shortfall.

---

### Finding Description

The `pay()` function has three branches for WETH payments: [1](#0-0) 

The middle branch (`nativeBalance > 0` but `< value`) at lines 78–81:

```solidity
} else if (nativeBalance > 0) {
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
}
```

This branch is designed for the **legitimate mixed-ETH/WETH** use case where the *same user* sends partial ETH with their swap call. However, the branch has no attribution check — it consumes **any** ETH sitting in the router, regardless of who deposited it.

ETH can be stranded in the router through the documented `multicall{value}` pattern. The test suite explicitly shows that users must include `refundETH()` in their multicall to recover unused ETH: [2](#0-1) 

If a user sends `multicall{value: X}([exactInputSingle(amountIn=Y)])` where `X > Y` and omits `refundETH()`, the excess `X - Y` ETH is stranded in the router. An attacker can then front-run the victim's `refundETH()` call (or simply observe the stranded balance) and call `exactInputSingle(tokenIn=WETH, amountIn=stranded + epsilon)` with only `epsilon` WETH approved. The partial-ETH branch fires, consuming the stranded ETH to subsidize the attacker's swap.

---

### Impact Explanation

**Direct loss of user ETH.** The victim's stranded ETH is irreversibly consumed to pay for the attacker's swap. The attacker receives the full swap output while only paying `amountIn - stranded_ETH` in WETH. The victim's `refundETH()` call will subsequently return 0.

Severity: **High** — unprivileged, direct principal loss, no special preconditions beyond a stranded ETH balance (which the protocol's own documented usage pattern can produce).

---

### Likelihood Explanation

The `multicall{value}` + `exactInput*` pattern is the documented ETH-input flow. Users who omit `refundETH()` (e.g., when sending exactly the right amount but the pool consumes slightly less, or when a prior step reverts mid-multicall leaving residual ETH) will strand ETH. An attacker monitoring the router's ETH balance can atomically exploit it in the same block.

---

### Recommendation

The partial-ETH branch must only consume ETH that was sent by the **current caller in the current transaction**. The cleanest fix is to track `msg.value` at the swap entrypoint and pass it as the authorized native contribution into `pay()`, rejecting any use of pre-existing router ETH balance that exceeds the current call's `msg.value`. Alternatively, enforce that `nativeBalance == msg.value` at the start of each payable entrypoint and revert if they diverge (indicating stranded ETH from a prior call).

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_strandedEthDrain() public {
    // 1. Victim sends multicall with excess ETH, forgets refundETH()
    vm.deal(victim, 1 ether);
    vm.prank(victim);
    bytes[] memory calls = new bytes[](1); // no refundETH!
    calls[0] = abi.encodeWithSelector(
        router.exactInputSingle.selector,
        ExactInputSingleParams({
            tokenIn: address(weth), amountIn: 0.5 ether, /* ... */
        })
    );
    router.multicall{value: 1 ether}(calls);
    // Router now holds 0.5 ETH residual

    // 2. Attacker has only 0.5 WETH approved, calls swap for 1 WETH
    weth.approve(address(router), 0.5 ether); // only half
    vm.prank(attacker);
    router.exactInputSingle(
        ExactInputSingleParams({tokenIn: address(weth), amountIn: 1 ether, /* ... */})
    );

    // 3. Assertions
    assertEq(address(router).balance, 0);       // router ETH drained
    assertEq(weth.balanceOf(attacker), initialWeth - 0.5 ether); // only 0.5 WETH pulled
    // victim's refundETH() now returns 0
}
```

The `pay()` partial branch at line 78–81 fires: it wraps the 0.5 ETH residual and sends it to the pool, then pulls only `1 ETH - 0.5 ETH = 0.5 WETH` from the attacker's approval. The pool receives the full 1 WETH and the attacker's swap executes at half cost. [3](#0-2)

### Citations

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
