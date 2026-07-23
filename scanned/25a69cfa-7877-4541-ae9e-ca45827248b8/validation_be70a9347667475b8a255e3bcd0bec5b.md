I have the full `pay()` implementation. Let me verify the exact logic and the `addLiquidityExactShares` callback path before rendering a verdict.

The code is fully in hand. Let me trace the exact execution path.

---

## Exact Code Trace

**`pay()` in `PeripheryPayments.sol` lines 69–88:**

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ALL ETH on contract
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance); // only pulls remainder
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

`address(this).balance` is the **total** ETH held by the adder — it does not distinguish ETH sent with the current call from ETH stranded by a prior call.

**How ETH gets stranded:** The `receive()` guard rejects direct ETH pushes, but ETH attached to a payable function call (e.g. `addLiquidityExactShares{value: X}(...)`) bypasses `receive()` entirely. If the pool only needs `Y < X` WETH, `pay()` wraps exactly `Y` and leaves `X - Y` on the contract. If the caller omits `refundETH()` from their multicall, that residual is permanently stranded.

**The exploit path:**

1. User A calls `addLiquidityExactShares{value: 1 ETH}(...)` where the pool requests 0.5 ETH worth of WETH. `pay()` wraps 0.5 ETH, sends 0.5 WETH to pool, leaves 0.5 ETH on adder. User A does not call `refundETH()`.
2. User B calls `addLiquidityExactShares` (no ETH sent) where the pool requests 1 ETH worth of WETH.
   - `nativeBalance = 0.5 ETH` (User A's residual)
   - Branch: `nativeBalance > 0` but `< value`
   - Wraps 0.5 ETH → sends 0.5 WETH to pool
   - Pulls only `0.5 WETH` from User B via `transferFrom`
   - Pool receives full 1 WETH; User B paid only 0.5 WETH
3. User A loses 0.5 ETH. User B receives a 0.5 ETH subsidy.

This is a real, direct fund-loss path. The invariant "payer must supply the full token amount" is broken whenever the adder holds residual ETH.

---

### Title
Residual ETH on `MetricOmmPoolLiquidityAdder` subsidises subsequent WETH payments, causing the original depositor to lose stranded ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay()` uses `address(this).balance` — the contract's total ETH balance — when settling a WETH payment. Any ETH left on the adder from a prior transaction is silently consumed to cover part of the next caller's WETH obligation, reducing the next caller's `transferFrom` pull by exactly the residual amount and permanently losing the original depositor's ETH.

### Finding Description
When `token == WETH` and `payer != address(this)`, `pay()` reads `nativeBalance = address(this).balance` and, if `0 < nativeBalance < value`, wraps the entire residual ETH balance and transfers it to the pool, then pulls only `value - nativeBalance` from the payer. [1](#0-0) 

ETH is stranded on the adder whenever a user calls `addLiquidityExactShares{value: X}(...)` and the pool requests `Y < X` WETH: `pay()` wraps exactly `Y`, leaving `X - Y` on the contract. [2](#0-1) 

The `receive()` guard only blocks direct ETH pushes; ETH attached to a payable function call is not subject to it. [3](#0-2) 

The callback in `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback` calls `pay(token0, payer, msg.sender, amount0Delta)` with `payer = msg.sender` of the outer `addLiquidityExactShares` call, so the residual ETH is consumed on behalf of a completely different user. [4](#0-3) 

### Impact Explanation
Direct loss of user principal. User A's excess ETH (stranded by not calling `refundETH()`) is consumed to subsidise User B's liquidity deposit. User A loses the stranded ETH with no recourse; User B receives liquidity at a discount. The loss is bounded only by the amount of ETH stranded, which can be up to the full `msg.value` of User A's call.

### Likelihood Explanation
Medium. The trigger condition — sending excess ETH without a `refundETH()` step — is a realistic user error, especially when calling `addLiquidityExactShares` directly rather than via `multicall`. The exploit requires no privilege, no malicious pool, and no non-standard token behaviour. Any subsequent WETH-leg liquidity addition automatically benefits from the residual.

### Recommendation
Track the ETH that belongs to the current call separately from pre-existing contract ETH. One approach: snapshot `address(this).balance - msg.value` at entry and treat only `msg.value` as available for wrapping. Alternatively, enforce that `address(this).balance` is zero at the start of every non-multicall entry point, or automatically refund unused ETH at the end of `_addLiquidity`.

### Proof of Concept
```solidity
// 1. Strand 0.5 ETH on the adder (User A sends excess ETH, no refundETH)
adder.addLiquidityExactShares{value: 1 ether}(pool, alice, 1, delta_needs_0_5_eth, ...);
// pay() wraps 0.5 ETH, leaves 0.5 ETH on adder

// 2. User B adds liquidity needing 1 ETH WETH, sends no ETH
uint256 wethBefore = weth.balanceOf(bob);
vm.prank(bob);
adder.addLiquidityExactShares(pool, bob, 2, delta_needs_1_eth, ...);

// pay() sees nativeBalance=0.5 ETH, wraps it, pulls only 0.5 WETH from bob
assertEq(wethBefore - weth.balanceOf(bob), 0.5 ether); // bob paid half
// adder ETH balance is now 0; User A's 0.5 ETH is gone
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-81)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-174)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
```
