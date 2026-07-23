The vulnerability is real. Let me trace the exact execution path:

**Trace:**

1. User A calls `exactInputSingle(tokenIn=WETH, amountIn=100)` with `msg.value=150`, without bundling a `refundETH()` call. The `pay()` WETH branch fires with `nativeBalance=150 >= value=100`, wraps 100 ETH, sends to pool. 50 ETH remains in the router. [1](#0-0) 

2. User B calls `exactInputSingle(tokenIn=WETH, amountIn=50)` with `msg.value=0`. `_setNextCallbackContext` records `payer=UserB`. [2](#0-1) 

3. Pool calls back `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, payer=UserB, pool, 50)`. [3](#0-2) 

4. Inside `pay()`: `payer != address(this)`, `token == WETH`, `nativeBalance = address(this).balance = 50 >= value = 50` → the `nativeBalance >= value` branch executes: wraps the 50 ETH (User A's) and transfers it to the pool. `safeTransferFrom(UserB, ...)` is **never called**. [4](#0-3) 

User B's swap is fully subsidized by User A's unrefunded ETH. User A loses 50 ETH.

---

### Title
Stranded ETH in router can be stolen by any caller via `exactInputSingle(tokenIn=WETH)` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay()` uses the router's entire native ETH balance to fund a WETH payment before pulling from the declared `payer`. Any ETH left in the router by a prior user (e.g., excess `msg.value` not reclaimed via `refundETH`) is consumed by the next caller's WETH swap at no cost to that caller.

### Finding Description
In `pay()`, when `token == WETH` and `payer != address(this)`, the function first checks `address(this).balance >= value`. If true, it wraps and transfers the router's native ETH balance to the pool without ever calling `safeTransferFrom(payer, ...)`. The `payer` field set by `_setNextCallbackContext` is completely bypassed. [5](#0-4) 

ETH accumulates in the router whenever a user sends `msg.value > amountIn` for a WETH swap and does not call `refundETH()` in the same multicall. This is a normal usage pattern (users may call `exactInputSingle` directly with excess ETH expecting a refund later, or a multicall may revert after the swap but before `refundETH`).

### Impact Explanation
**High.** Direct theft of user principal. Any ETH stranded in the router is claimable by any subsequent caller who submits `exactInputSingle(tokenIn=WETH, amountIn=<stranded_amount>)` with `msg.value=0`. The attacker receives the full swap output while paying nothing; the prior user permanently loses their ETH.

### Likelihood Explanation
**Medium.** The precondition — ETH left in the router — arises naturally when users call `exactInputSingle` with excess `msg.value` without a same-transaction `refundETH`, or when a multicall partially fails. The exploit itself requires no special permissions and is trivially executable by any EOA or bot monitoring the router's ETH balance.

### Recommendation
Remove the native-ETH-first logic from `pay()` for the case where `payer` is an external user. The router's ETH balance should only be used when `payer == address(this)` (i.e., mid-path, where the router legitimately holds intermediate tokens/ETH). For an external payer requesting a WETH swap, always use `safeTransferFrom(payer, recipient, value)` directly:

```solidity
} else if (token == WETH) {
    if (payer == address(this)) {
        // mid-path: router holds ETH from a prior wrap
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

If ETH-as-WETH-input is a desired UX feature, it must be handled explicitly at the `exactInputSingle` entry point (e.g., wrap `msg.value` into WETH on behalf of `msg.sender` before the swap, then refund any excess), not silently inside the callback payment path.

### Proof of Concept
```solidity
// Foundry fork test sketch
function test_strandedEthStolenViaWethSwap() public {
    // User A: sends 150 ETH, swaps 100 WETH, leaves 50 ETH in router
    vm.deal(userA, 150 ether);
    vm.prank(userA);
    router.exactInputSingle{value: 150 ether}(ExactInputSingleParams({
        tokenIn: WETH, amountIn: 100 ether, /* ... */ recipient: userA
    }));
    // User A forgot refundETH; router now holds 50 ETH
    assertEq(address(router).balance, 50 ether);

    // User B: sends 0 ETH, swaps 50 WETH — paid by User A's stranded ETH
    uint256 wethBefore = IERC20(WETH).balanceOf(userB_wallet); // approve not needed
    vm.prank(userB);
    router.exactInputSingle{value: 0}(ExactInputSingleParams({
        tokenIn: WETH, amountIn: 50 ether, /* ... */ recipient: userB
    }));
    // Router ETH is now 0; User B received swap output; transferFrom(userB) was never called
    assertEq(address(router).balance, 0);
    assertEq(IERC20(WETH).balanceOf(userB_wallet), wethBefore); // no WETH pulled from B
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
