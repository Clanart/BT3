### Title
Router Residual ETH Subsidizes Attacker WETH Swaps via `PeripheryPayments.pay()` Partial-ETH Path — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` function's partial-ETH branch uses `address(this).balance` — the router's **total** native ETH balance — to partially fund a WETH payment on behalf of the current caller. Because the router cannot distinguish ETH sent by the current caller from ETH left behind by a prior user, any residual ETH in the router is silently consumed to subsidize the attacker's swap, reducing the WETH pulled from the attacker's approval by exactly that amount.

---

### Finding Description

`pay()` in `PeripheryPayments.sol` handles WETH payments through three branches: [1](#0-0) 

When `token == WETH`, `payer != address(this)`, and `0 < address(this).balance < value`, the partial-ETH branch executes:

1. Deposits `nativeBalance` ETH as WETH and transfers it to the pool.
2. Pulls only `value - nativeBalance` WETH from the payer via `safeTransferFrom`.

This is the **intended** mixed-ETH/WETH path, validated by the test `test_mixedNativeAndWeth_exactInputSingle_wethForToken`. [2](#0-1) 

The flaw is that `address(this).balance` is the router's **aggregate** balance — it includes ETH from any prior caller who overpaid and did not call `refundETH()`. The router has no mechanism to attribute ETH to a specific caller within a transaction.

**How residual ETH accumulates:** A user calls `multicall{value: 2 ether}` with a 1 ether WETH swap but omits `refundETH()`. The test `test_multicall_ethInput_exactInputSingle_refundsUnusedEth` shows this is a real pattern — users are expected to include `refundETH()` but are not forced to. [3](#0-2) 

**Attack path:**

1. Alice calls `multicall{value: 2 ether}` with `exactInputSingle(tokenIn=WETH, amountIn=1 ether)` and no `refundETH()`. Router retains 1 ETH.
2. Bob calls `exactInputSingle(tokenIn=WETH, amountIn=1 ether)` with 0 ETH sent.
3. `_justPayCallback` calls `pay(WETH, Bob, pool, 1 ether)`. [4](#0-3) 

4. Inside `pay()`: `nativeBalance = 1 ether >= value = 1 ether` → first branch fires: router deposits its 1 ETH as WETH, transfers to pool, pulls **0 WETH** from Bob.
5. Bob receives the full swap output. Alice's 1 ETH is gone. Bob's WETH approval is untouched.

If `nativeBalance = 0.5 ether < value = 1 ether`, the partial branch fires: Bob pays only 0.5 WETH instead of 1 WETH, and Alice's 0.5 ETH is consumed.

The callback context correctly records `payer = msg.sender` (Bob) and `tokenToPay = WETH`, but `pay()` never verifies that the ETH it is spending was contributed by Bob. [5](#0-4) 

---

### Impact Explanation

**Direct loss of user funds.** Any ETH left in the router by a prior user (via overpayment without `refundETH()`) is silently transferred to the pool as part of an attacker's WETH swap. The attacker receives the full swap output while paying less WETH than the pool demanded. The prior user's ETH is permanently lost. This satisfies the "direct loss of user principal" threshold at HIGH severity.

---

### Likelihood Explanation

**Medium.** The attack requires residual ETH in the router. This is a realistic condition: the `refundETH()` call is optional and user-driven; any user who sends excess ETH via `multicall` without including `refundETH()` leaves ETH behind. The attack itself is then trivially executable by any address with a WETH approval.

---

### Recommendation

Track the ETH contributed by the **current** call context rather than using the aggregate `address(this).balance`. One approach: pass `msg.value` through the call stack and cap the ETH used in `pay()` to that amount. Alternatively, require that the partial-ETH path only fires when `payer == address(this)` (i.e., the router itself is the payer, meaning it already holds the funds on behalf of the current user from the same transaction).

```solidity
// In pay(), replace:
uint256 nativeBalance = address(this).balance;
// With a caller-scoped ETH budget passed from the entry point:
uint256 nativeBalance = _callerEthBudget; // tracked per-call via transient storage
```

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_residualEth_subsidizesAttackerWethSwap() public {
    // Seed router with 0.5 ETH residual (simulates Alice forgetting refundETH)
    vm.deal(address(router), 0.5 ether);

    uint128 amountIn = 1 ether;
    uint256 attackerWethBefore = weth.balanceOf(attacker);
    uint256 routerEthBefore = address(router).balance; // 0.5 ether

    vm.prank(attacker);
    // Attacker sends 0 ETH, has WETH approval for full 1 ether
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,       // 1 ether
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));

    // Router ETH drained to zero
    assertEq(address(router).balance, 0, "router ETH drained");
    // Attacker only paid 0.5 WETH, not 1 WETH
    assertEq(attackerWethBefore - weth.balanceOf(attacker), 0.5 ether, "only 0.5 WETH pulled");
}
```

The pool receives the full 1 WETH (0.5 from router ETH + 0.5 from attacker's approval). The attacker's swap is fully settled. Alice's 0.5 ETH is permanently consumed.

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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L41-70)
```text
  function test_mixedNativeAndWeth_exactInputSingle_wethForToken() public {
    uint128 amountIn = 2_500;
    uint256 nativePart = amountIn / 2;
    uint256 wethPart = amountIn - nativePart;

    uint256 token1Before = token1.balanceOf(recipient);
    uint256 swapperEthBefore = swapper.balance;
    uint256 swapperWethBefore = weth.balanceOf(swapper);

    vm.prank(swapper);
    router.exactInputSingle{value: nativePart}(
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

    assertGt(token1.balanceOf(recipient) - token1Before, 0, "recipient token1");
    assertEq(swapperEthBefore - swapper.balance, nativePart, "swapper native spent");
    assertEq(swapperWethBefore - weth.balanceOf(swapper), wethPart, "swapper weth spent");
    _assertRouterEmpty();
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
