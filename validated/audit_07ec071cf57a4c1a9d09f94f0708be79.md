The vulnerability is real. Let me confirm the exact code path before writing the finding.

### Title
Unguarded `refundETH()` allows any caller to drain ETH left in the router from a prior user's multicall — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.refundETH()` transfers the router's entire native ETH balance to `msg.sender` with no access control. ETH sent as `msg.value` in a `multicall{value: X}` call is not fully consumed when `amountIn < X` (only `amountIn` is deposited into WETH by `pay()`). The residual ETH persists in the router across transaction boundaries. Any subsequent caller of `refundETH()` in a separate transaction receives that ETH.

---

### Finding Description

`refundETH()` is unconditionally callable by any EOA or contract: [1](#0-0) 

It sends `address(this).balance` — the full native ETH balance — to `msg.sender` with no check that the caller is the original depositor.

ETH enters the router as `msg.value` on `multicall` or any `payable` swap function. The `receive()` guard only blocks plain ETH pushes from non-WETH addresses; it does not prevent ETH from arriving as `msg.value` in a call: [2](#0-1) 

Inside `pay()`, when `token == WETH` and the router holds native ETH, only exactly `value` (the swap's `amountIn`) is deposited into WETH. Any excess `msg.value` above `amountIn` is left untouched in the router's balance: [3](#0-2) 

The intended pattern (documented in `IMetricOmmPoolLiquidityAdder`) is that users include `refundETH()` as the last call in their multicall batch. But this is not enforced. If a user omits it, the residual ETH persists in the router after the transaction ends, and any attacker can claim it in the next block.

---

### Impact Explanation

Direct theft of user ETH principal. An attacker monitoring the mempool (or simply polling the router's ETH balance) can call `refundETH()` immediately after any multicall that leaves a non-zero ETH residual. The attacker receives the full router balance — which belongs entirely to the prior user. There is no slippage, no oracle, and no privileged role required.

---

### Likelihood Explanation

The trigger condition is a user sending `multicall{value: X}` with `amountIn < X` and omitting `refundETH()` from the batch. This is a realistic user error (e.g., sending a round-number ETH value for a swap that consumes a fractional amount, or a frontend that miscalculates the exact input). The attack requires only a standard external call with no setup.

---

### Recommendation

Restrict `refundETH()` so it can only be called within the same multicall context as the ETH deposit, or track per-sender ETH deposits and only refund the recorded sender. The simplest safe fix is to remove the standalone `external` entrypoint and only expose refund logic as an `internal` helper callable from within `multicall` — matching the pattern used by Uniswap v3 periphery where `refundETH` is safe only because it is always bundled atomically with the swap in the same transaction.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Foundry test sketch
function test_attacker_steals_victim_residual_eth() public {
    address victim  = makeAddr("victim");
    address attacker = makeAddr("attacker");
    vm.deal(victim, 1 ether);

    // Victim sends 1 ETH but only swaps 0.5 ETH worth of WETH; omits refundETH()
    vm.prank(victim);
    bytes[] memory calls = new bytes[](1);
    calls[0] = abi.encodeWithSelector(
        router.exactInputSingle.selector,
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 0.5 ether,   // only 0.5 ETH consumed
            amountOutMinimum: 0,
            recipient: victim,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    router.multicall{value: 1 ether}(calls); // sends 1 ETH, 0.5 ETH left in router

    assertEq(address(router).balance, 0.5 ether, "residual ETH in router");

    // Attacker calls refundETH() in a separate transaction
    vm.prank(attacker);
    router.refundETH();

    assertEq(attacker.balance, 0.5 ether, "attacker stole victim ETH");
    assertEq(address(router).balance, 0,   "router drained");
}
```

The `pay()` branch at [4](#0-3)  deposits only `value` (0.5 ETH), leaving the remaining 0.5 ETH in `address(this).balance`. The unguarded `refundETH()` at [1](#0-0)  then transfers it to the attacker.

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
```
