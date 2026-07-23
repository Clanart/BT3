The `refundETH()` implementation and the ETH handling in `pay()` confirm this is a real vulnerability. Let me trace the exact path.

### Title
Unguarded `refundETH()` allows any caller to steal excess ETH stranded on the router by a prior user's incomplete multicall — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`refundETH()` is a public, access-control-free function that unconditionally transfers the router's entire ETH balance to `msg.sender`. Because `pay()` only wraps exactly the swap amount when native ETH is used as WETH input, any excess ETH sent with a `multicall{value}` call that omits `refundETH` is permanently stranded on the router and claimable by any subsequent caller in a separate transaction.

---

### Finding Description

`refundETH()` contains no ownership check: [1](#0-0) 

It sends `address(this).balance` to `msg.sender` with no verification that the caller is the one who deposited the ETH.

When a user swaps with native ETH as WETH input, `pay()` deposits only the exact swap amount: [2](#0-1) 

The remainder of `msg.value` stays on the router. The `receive()` guard only blocks plain ETH transfers (no calldata); it does **not** prevent ETH from being attached to `payable` function calls like `multicall{value: X}(...)` or `exactInputSingle{value: X}(...)`. [3](#0-2) 

The intended safe pattern is to include `refundETH` as the last call in the same `multicall`, as shown in the test suite: [4](#0-3) 

But if a user omits it — either by mistake or by calling `exactInputSingle{value}` directly without a multicall wrapper — the excess ETH is left on the router with no attribution and is immediately claimable by anyone.

---

### Impact Explanation

Direct theft of user ETH. Any ETH stranded on the router (from excess `msg.value` in a swap that consumed less than the full amount) can be drained by an attacker calling `refundETH()` in the next transaction. There is no minimum threshold — the attacker receives the full stranded balance. This is a Critical-severity direct fund loss.

---

### Likelihood Explanation

The pattern of sending excess ETH and relying on `refundETH` in the same multicall is the documented and tested usage pattern. Users calling `exactInputSingle{value}` directly (without multicall) or forgetting to append `refundETH` will strand ETH. MEV bots monitoring the mempool or block state can trivially detect a non-zero router ETH balance and call `refundETH()` atomically in the next block.

---

### Recommendation

Restrict `refundETH()` so it can only be called within a `multicall` context (i.e., via `delegatecall` from `multicall`), or record the original `msg.sender` of the outermost `multicall` in transient storage and require `msg.sender == storedCaller` inside `refundETH`. Alternatively, accept a `recipient` parameter and restrict it to a caller-supplied address validated against the transient payer context, mirroring how `unwrapWETH9` accepts a `recipient` but is called within the same atomic multicall.

---

### Proof of Concept

```
1. User calls router.exactInputSingle{value: 1 ether}(
       ExactInputSingleParams({
           tokenIn: WETH, amountIn: 0.5 ether, ...
       })
   );
   // pay() deposits 0.5 ETH as WETH → pool; 0.5 ETH remains on router

2. Attacker (separate tx) calls router.refundETH();
   // refundETH: balance = 0.5 ETH, _transferETH(attacker, 0.5 ETH)
   // Attacker receives 0.5 ETH; user's excess is gone.
```

The existing test `test_refundETH_sendsBalanceToCaller` directly confirms this behavior — it pre-loads the router with ETH via `vm.deal` and shows any caller receives it: [5](#0-4)

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

**File:** metric-periphery/test/MetricOmmSimpleRouter.payments.t.sol (L74-85)
```text
  function test_refundETH_sendsBalanceToCaller() public {
    uint256 amount = 2 ether;
    vm.deal(address(router), amount);

    uint256 swapperBefore = swapper.balance;

    vm.prank(swapper);
    router.refundETH();

    assertEq(swapper.balance - swapperBefore, amount, "swapper refunded");
    assertEq(address(router).balance, 0, "router eth cleared");
  }
```
