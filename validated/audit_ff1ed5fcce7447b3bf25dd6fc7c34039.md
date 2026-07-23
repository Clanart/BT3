The code confirms all elements of the claim. Let me verify the key facts:

1. `addLiquidityExactShares` is `payable` — confirmed at lines 56 and 71 of `MetricOmmPoolLiquidityAdder.sol`.
2. `pay()` uses `address(this).balance` (total ETH, not just `msg.value`) — confirmed at line 74 of `PeripheryPayments.sol`.
3. When `nativeBalance >= value`, only `value` is wrapped, leaving the excess on the contract — confirmed at lines 75–77.
4. The `receive()` guard only blocks direct ETH pushes, not ETH attached to a function call — confirmed at lines 32–34.
5. The callback passes `payer = msg.sender` of the outer call, so a different user's residual ETH is consumed — confirmed at lines 162 and 173.
6. `refundETH()` exists but is not automatically called — confirmed at lines 58–63.

All preconditions, the exploit path, and the broken invariant are verified against production code.

---

Audit Report

## Title
Residual ETH on `MetricOmmPoolLiquidityAdder` subsidises subsequent WETH payments, causing the original depositor to lose stranded ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` reads `address(this).balance` — the contract's total ETH balance — when settling a WETH payment. Any ETH left on the adder from a prior transaction is silently consumed to cover part of the next caller's WETH obligation, reducing the next caller's `transferFrom` pull by exactly the residual amount and permanently losing the original depositor's ETH.

## Finding Description
`addLiquidityExactShares` is `payable`. [1](#0-0) 

When `token == WETH` and `payer != address(this)`, `pay()` reads `nativeBalance = address(this).balance`. If `nativeBalance >= value`, it wraps only `value` and leaves the remainder on the contract with no automatic refund. [2](#0-1) 

If `0 < nativeBalance < value`, it wraps the entire residual, sends it to the pool, then pulls only `value - nativeBalance` from the current payer — consuming a prior user's ETH on behalf of a completely different user. [3](#0-2) 

The `receive()` guard only blocks direct ETH pushes; ETH attached to a payable function call is not subject to it, so ETH stranding via `addLiquidityExactShares{value: X}(...)` is a normal user path. [4](#0-3) 

The callback in `metricOmmModifyLiquidityCallback` calls `pay(token0, payer, msg.sender, amount0Delta)` where `payer` is the `msg.sender` of the outer `addLiquidityExactShares` call, so the residual ETH is consumed on behalf of a completely different user. [5](#0-4) 

`refundETH()` exists but is not called automatically at the end of `_addLiquidity`; it must be explicitly included in a `multicall` by the user. [6](#0-5) 

**Exploit path:**
1. User A calls `addLiquidityExactShares{value: 1 ETH}(...)` where the pool requests 0.5 ETH worth of WETH. `pay()` wraps 0.5 ETH, sends 0.5 WETH to pool, leaves 0.5 ETH on the adder. User A does not call `refundETH()`.
2. User B calls `addLiquidityExactShares` (no ETH) where the pool requests 1 ETH worth of WETH. `nativeBalance = 0.5 ETH > 0` but `< 1 ETH`. `pay()` wraps 0.5 ETH → sends 0.5 WETH to pool, then pulls only 0.5 WETH from User B. Pool receives full 1 WETH; User B paid only 0.5 WETH. User A's 0.5 ETH is permanently lost.

## Impact Explanation
Direct loss of user principal. User A's excess ETH (stranded by not calling `refundETH()`) is consumed to subsidise User B's liquidity deposit. User A loses the stranded ETH with no recourse; User B receives liquidity at a discount. The loss is bounded only by the amount of ETH stranded, which can be up to the full `msg.value` of User A's call.

## Likelihood Explanation
Medium. The trigger condition — sending excess ETH without a `refundETH()` step — is a realistic user error, especially when calling `addLiquidityExactShares` directly rather than via `multicall`. The exploit requires no privilege, no malicious pool, and no non-standard token behaviour. Any subsequent WETH-leg liquidity addition automatically benefits from the residual.

## Recommendation
Track the ETH that belongs to the current call separately from pre-existing contract ETH. One approach: snapshot `address(this).balance - msg.value` at entry and treat only `msg.value` as available for wrapping. Alternatively, automatically call `refundETH()` at the end of `_addLiquidity`, or enforce that `address(this).balance` is zero at the start of every non-multicall entry point.

## Proof of Concept
```solidity
// 1. Strand 0.5 ETH on the adder (User A sends excess ETH, no refundETH)
vm.prank(alice);
adder.addLiquidityExactShares{value: 1 ether}(pool, alice, 1, delta_needs_0_5_eth, 1 ether, 0, "");
// pay() wraps 0.5 ETH, leaves 0.5 ETH on adder

// 2. User B adds liquidity needing 1 ETH WETH, sends no ETH
uint256 wethBefore = weth.balanceOf(bob);
vm.prank(bob);
adder.addLiquidityExactShares(pool, bob, 2, delta_needs_1_eth, 1 ether, 0, "");

// pay() sees nativeBalance=0.5 ETH, wraps it, pulls only 0.5 WETH from bob
assertEq(wethBefore - weth.balanceOf(bob), 0.5 ether); // bob paid half
assertEq(address(adder).balance, 0);                   // alice's 0.5 ETH is gone
```

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-64)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-173)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
```

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-84)
```text
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
