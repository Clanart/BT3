Audit Report

## Title
Unprotected `refundETH()` allows any caller to steal excess ETH left in `MetricOmmPoolLiquidityAdder` after a WETH-pool liquidity deposit — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`refundETH()` has no access control and unconditionally transfers the contract's entire native ETH balance to `msg.sender`. Because `addLiquidityExactShares` is `payable` and `pay()` wraps only the exact ETH amount needed for the pool (leaving any overage in the contract), any attacker can call `refundETH()` in a subsequent transaction and steal the unspent ETH before the original depositor can reclaim it.

## Finding Description

`refundETH()` sends the full `address(this).balance` to `msg.sender` with no caller restriction: [1](#0-0) 

When `token == WETH` and the contract holds more native ETH than the pool requires, `pay()` wraps only `value` (the exact amount needed) and leaves the remainder in the contract: [2](#0-1) 

`addLiquidityExactShares` is `payable` and passes `msg.sender` as payer, but performs no ETH sweep after the pool call returns: [3](#0-2) 

`_addLiquidity` likewise performs no ETH refund after the pool call completes: [4](#0-3) 

The `receive()` guard only blocks direct ETH sends from non-WETH addresses; it does not prevent ETH from accumulating via `payable` function calls: [5](#0-4) 

The `multicall` function exists and could be used to bundle `addLiquidityExactShares` + `refundETH()` atomically, but this is not enforced, and users who do not bundle are fully exposed: [6](#0-5) 

## Impact Explanation

Any user who sends more ETH than the pool consumes loses the difference permanently to the first caller of `refundETH()`. This is a direct, complete loss of user principal with no protocol-side protection. The impact qualifies as High under Sherlock thresholds: arbitrary ETH amounts can be stolen from any WETH-pool liquidity depositor who does not bundle the refund in the same `multicall`.

## Likelihood Explanation

Sending a small ETH buffer above the exact required amount is standard practice to avoid reverts from minor price movement. The attack requires only a single public call with no special privileges. A mempool-watching bot can front-run the user's own `refundETH()` call, or simply call it first if the user does not bundle the refund in the same `multicall`. The attack is repeatable against every such deposit.

## Recommendation

The simplest fix is to auto-refund in `_addLiquidity` after the pool call returns: push any remaining `address(this).balance` back to `payer`. Alternatively, restrict `refundETH()` to a tracked recipient stored in transient storage alongside the pay context, or document and enforce that `addLiquidityExactShares` must always be called inside a `multicall` ending with `refundETH()` and add a post-call check that no ETH remains.

## Proof of Concept

```
1. Pool: token0 = WETH, token1 = USDC.
2. User calls addLiquidityExactShares{value: 2 ether}(...) where the pool
   only needs 1 ether of WETH.
3. pay() wraps 1 ether → WETH → pool. 1 ether remains in the contract.
4. addLiquidityExactShares returns successfully.
5. Attacker calls refundETH() → receives 1 ether.
6. User calls refundETH() → receives nothing (balance is 0).
```

Foundry test: deploy `MetricOmmPoolLiquidityAdder`, mock a WETH pool that requests 1 ether in the callback, call `addLiquidityExactShares{value: 2 ether}`, then call `refundETH()` from a separate address and assert it receives 1 ether while the original caller receives 0.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L42-47)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
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
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L193-207)
```text
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
  }
```
