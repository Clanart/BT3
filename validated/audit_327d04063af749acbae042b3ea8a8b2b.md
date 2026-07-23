Audit Report

## Title
Untracked ETH Accumulates in Payable Periphery Contracts and Is Drainable by Any Caller via Unguarded `refundETH()` — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`, `metric-periphery/contracts/MetricOmmSimpleRouter.sol`, `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary
Every swap and liquidity function in the periphery layer is `payable` and silently accepts ETH even when the operation involves no WETH. The internal `pay()` function consumes `address(this).balance` (the entire contract ETH balance) rather than the per-call `msg.value`, so ETH deposited by one user can be consumed by a subsequent user's WETH swap. `refundETH()` carries no access control and unconditionally transfers the full contract ETH balance to `msg.sender`, allowing any third party to drain ETH that was deposited by a different user.

## Finding Description
`PeripheryPayments.receive()` reverts for direct ETH transfers from non-WETH senders:

```solidity
// PeripheryPayments.sol L32-34
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
```

This guard is irrelevant when ETH is sent alongside a `payable` function call — `receive()` is not invoked in that case. Every externally-callable function in `MetricOmmSimpleRouter` (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`) and `MetricOmmPoolLiquidityAdder` (`addLiquidityExactShares` ×2, `addLiquidityWeighted` ×2, `multicall`) is `payable` and accepts ETH unconditionally. `selfPermit`, `selfPermitIfNecessary`, `selfPermitAllowed`, `selfPermitAllowedIfNecessary` in `SelfPermit.sol` are also `payable` but never consume ETH under any circumstances.

When `pay()` is invoked for a non-WETH token, the ETH is silently ignored:

```solidity
// PeripheryPayments.sol L85-87
} else {
    IERC20(token).safeTransferFrom(payer, recipient, value);
}
```

When `pay()` is invoked for WETH, it reads `address(this).balance` — the entire contract balance — rather than the ETH contributed by the current caller:

```solidity
// PeripheryPayments.sol L74
uint256 nativeBalance = address(this).balance;
```

`refundETH()` has no access control and sends the full contract ETH balance to `msg.sender`:

```solidity
// PeripheryPayments.sol L58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

**Exploit path A (ETH theft):**
1. User A calls `addLiquidityExactShares(pool=DAI_USDC, ...)` and sends 1 ETH. The function is `payable`; ETH is accepted.
2. In `metricOmmModifyLiquidityCallback`, `pay(DAI, userA, pool, amount0Delta)` executes the `safeTransferFrom` branch. The 1 ETH is untouched.
3. User B calls `refundETH()`. The function sends `address(this).balance` (1 ETH) to User B. User A's ETH is permanently lost.

**Exploit path B (cross-user ETH subsidy):**
1. User A calls `exactInputSingle(tokenIn=WETH, amountIn=0.5 ETH)` and sends 1 ETH. `pay()` wraps 0.5 ETH; 0.5 ETH remains in the contract.
2. User B calls `exactInputSingle(tokenIn=WETH, amountIn=0.5 ETH)` with 0 ETH sent.
3. In the swap callback, `pay(WETH, userB, pool, 0.5 ETH)` reads `address(this).balance == 0.5 ETH`, takes the `nativeBalance >= value` branch, wraps and transfers User A's residual ETH on behalf of User B. User B receives swap output without paying.

## Impact Explanation
Direct loss of user ETH principal. In Exploit A, any ETH sent with a non-WETH `payable` call is permanently accessible to an unprivileged third party via a single zero-cost call to `refundETH()`. In Exploit B, residual ETH from a WETH swap is silently consumed to subsidize a different user's swap obligation, with no recourse for the original depositor. Both impacts constitute direct loss of user funds above Sherlock thresholds.

## Likelihood Explanation
All swap and liquidity entry points are `payable`, which signals to users and integrators that ETH is accepted. Users adding liquidity to a WETH pool may send ETH; if the pool is non-WETH, the ETH is silently trapped. Integrators composing `multicall` batches may send ETH for one leg and leave residual ETH after partial WETH consumption. `selfPermit*` functions are `payable` but never consume ETH; any ETH sent with them is always wasted and immediately drainable. The drain attack requires zero privilege and is a single external call, making it trivially repeatable by any on-chain observer watching the contract's ETH balance.

## Recommendation
1. **Remove `payable` from functions that never consume ETH**: `sweepToken`, `unwrapWETH9`, `selfPermit`, `selfPermitIfNecessary`, `selfPermitAllowed`, `selfPermitAllowedIfNecessary`. These are designed for `multicall` composition but do not themselves consume ETH.
2. **Replace `address(this).balance` with `msg.value` tracking in `pay()`**: Pass the ETH contributed by the current top-level call as a parameter or transient slot, and consume only that amount. This prevents cross-user ETH consumption.
3. **Restrict `refundETH()` to return ETH only to the original depositor**, or enforce that it is always called in the same `multicall` batch as the ETH-consuming operation (e.g., by recording the depositor address in transient storage and checking `msg.sender` against it).

## Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Foundry fork test outline
contract ETHTheftTest is Test {
    MetricOmmPoolLiquidityAdder adder;
    address DAI_USDC_POOL; // non-WETH pool

    function test_refundETH_steals_userA_eth() public {
        address userA = makeAddr("userA");
        address userB = makeAddr("userB");
        vm.deal(userA, 1 ether);

        // User A calls addLiquidityExactShares on a non-WETH pool, accidentally sends 1 ETH
        vm.prank(userA);
        adder.addLiquidityExactShares{value: 1 ether}(
            DAI_USDC_POOL, userA, 0, deltas, maxAmt0, maxAmt1, ""
        );

        // Contract now holds 1 ETH; User A's ETH is stuck
        assertEq(address(adder).balance, 1 ether);

        // User B drains it with a single call
        vm.prank(userB);
        adder.refundETH();

        assertEq(address(userB).balance, 1 ether); // User B stole User A's ETH
        assertEq(address(adder).balance, 0);
    }
}
```