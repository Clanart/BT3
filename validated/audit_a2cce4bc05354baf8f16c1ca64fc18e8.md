Audit Report

## Title
Unattributed `address(this).balance` in `PeripheryPayments.pay()` WETH branch allows stranded ETH from one user to fund another user's WETH liquidity payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay()` reads the contract's total native ETH balance (`address(this).balance`) when settling WETH payments, with no per-caller attribution. Because all `addLiquidity*` and `multicall` entry points are `payable`, ETH sent with a non-WETH pool call is never consumed and remains on the contract. Any subsequent WETH-pool liquidity call will silently consume that stranded ETH, causing a direct, unrecoverable loss of principal for the original depositor.

## Finding Description

In `pay()`, the WETH branch reads the full contract balance: [1](#0-0) 

There is no mapping of ETH to the `payer` argument — any ETH on the contract is eligible to fund any caller's WETH payment. ETH reaches the contract via `payable` entry points (`addLiquidityExactShares`, `addLiquidityWeighted`, `multicall`, `refundETH`, `unwrapWETH9`, `sweepToken`). The `receive()` guard only blocks plain ETH transfers (no calldata); ETH sent via `msg.value` in a function call bypasses it entirely: [2](#0-1) 

When User A calls `addLiquidityExactShares{value: X}` for a non-WETH pool, neither token triggers the WETH branch in `pay()`, so `X` ETH is never consumed and sits on the contract. `refundETH()` sends the entire balance to `msg.sender` — whichever of `refundETH()` or a WETH-pool `pay()` call executes first consumes User A's ETH: [3](#0-2) 

The callback that triggers `pay()` is: [4](#0-3) 

## Impact Explanation

Direct, cross-user loss of ETH principal with no recovery path once consumed. If User A's stranded ETH equals or exceeds the WETH amount User B's pool requests, User B pays zero from their own allowance and User A loses the full amount. If partial, User A loses the partial amount. This is a **High** severity direct loss of user principal reachable by any unprivileged caller.

## Likelihood Explanation

The pattern of sending ETH with a liquidity call and relying on a separate `refundETH()` step is the standard Uniswap-style router UX. Any user who sends ETH with a non-WETH pool call, sends excess ETH with a WETH pool call, or uses `multicall` with ETH and omits `refundETH()` as the last step leaves ETH on the contract. A MEV bot observing the mempool can immediately follow with a WETH-pool liquidity call to consume it atomically. No special privileges are required.

## Recommendation

Track per-caller ETH deposits in transient storage alongside the existing pay context (`T_SLOT_PAY_PAYER`). In the WETH branch of `pay()`, only consume ETH attributed to the current `payer` (i.e., the amount stored for that caller), not the full `address(this).balance`. Alternatively, pass `msg.value` explicitly through the call stack and consume only that amount within the same call frame, never reading the aggregate contract balance for payment settlement.

## Proof of Concept

1. User A calls `addLiquidityExactShares{value: 1 ether}(nonWethPool, ...)`. Pool requests ERC20 tokens only; `pay()` takes the `safeTransferFrom` branch for both tokens. 1 ETH remains on the contract.
2. Attacker calls `addLiquidityExactShares(wethPool, ...)` (no ETH sent) for a pool where `token0 = WETH` and the pool requests `amount0Delta = 1e18`.
3. `metricOmmModifyLiquidityCallback` loads `payer = attacker`, calls `pay(WETH, attacker, wethPool, 1e18)`.
4. `nativeBalance = 1 ether >= 1e18` → `IWETH9.deposit{value: 1 ether}()` + `safeTransfer(wethPool, 1e18)`. Attacker's WETH allowance is not touched.
5. User A calls `refundETH()` — returns 0. 1 ETH is permanently lost.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L57-63)
```text
  /// @inheritdoc IPeripheryPayments
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```
