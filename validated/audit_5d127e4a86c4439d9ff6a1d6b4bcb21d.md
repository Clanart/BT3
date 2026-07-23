Audit Report

## Title
Unprotected `refundETH()` allows any caller to drain excess ETH left in `MetricOmmPoolLiquidityAdder` after a WETH-pool liquidity deposit — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`refundETH()` is an unrestricted external function that transfers the contract's entire native ETH balance to `msg.sender`. When a user calls `addLiquidityExactShares` with ETH for a WETH pool, `pay()` wraps only the exact amount needed and leaves any surplus in the contract. An attacker can immediately call `refundETH()` in a subsequent transaction to steal that surplus, causing a direct, complete loss of the user's excess principal.

## Finding Description

`refundETH()` has no caller restriction and unconditionally sends `address(this).balance` to `msg.sender`: [1](#0-0) 

When `token == WETH` and the contract holds native ETH, `pay()` wraps exactly `value` ETH and transfers it to the pool — any ETH above `value` that the user sent remains in the contract: [2](#0-1) 

`addLiquidityExactShares` is `payable` and performs no automatic ETH refund after `_addLiquidity` returns: [3](#0-2) 

`_addLiquidity` also performs no ETH sweep after the pool call completes: [4](#0-3) 

The `receive()` guard only blocks direct ETH sends from non-WETH addresses; it does not prevent ETH from accumulating via `payable` function calls: [5](#0-4) 

The `multicall` helper exists but there is no enforcement or documentation requiring users to bundle `refundETH()` with their deposit call, leaving the race condition fully exploitable: [6](#0-5) 

## Impact Explanation

A user who sends `N` ETH to `addLiquidityExactShares` for a WETH pool where only `M < N` ETH is consumed permanently loses `N - M` ETH to any address that calls `refundETH()` first. This is a direct, complete loss of user principal with no protocol-side protection. Sending a small ETH buffer above the exact required amount is standard practice to avoid reverts from minor price movement, making this loss routine rather than edge-case.

## Likelihood Explanation

The attack requires only a single public call with no special privileges. A bot watching the mempool can front-run the user's own `refundETH()` call, or simply call it first if the user does not bundle the refund in the same `multicall`. The precondition — a WETH-pool deposit with any ETH overage — is a normal, expected usage pattern.

## Recommendation

The simplest fix is to auto-refund in `_addLiquidity`: after the pool call returns successfully, push any remaining `address(this).balance` back to `payer`. This eliminates the race condition entirely without requiring users to construct a `multicall`. Alternatively, restrict `refundETH()` to a tracked recipient by recording the depositor in transient storage alongside the pay context and enforcing `msg.sender == depositor` inside `refundETH()`.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
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
