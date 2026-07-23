### Title
Stranded Native ETH on `MetricOmmPoolLiquidityAdder` Is Freely Claimable as WETH Liquidity by Any Subsequent Caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` — the contract's **total** native ETH balance — when settling WETH payments. This balance is not scoped to the current transaction's `msg.value`. Any ETH left on the `MetricOmmPoolLiquidityAdder` contract from a prior caller's overpayment is silently consumed by the next caller who adds liquidity to a WETH pool, giving that caller free liquidity at the prior caller's expense.

---

### Finding Description

The `pay` helper in `PeripheryPayments` has three branches for WETH settlement: [1](#0-0) 

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

`address(this).balance` is the **contract-wide** native ETH balance, not the current call's `msg.value`. ETH accumulates on `MetricOmmPoolLiquidityAdder` whenever a caller sends more `msg.value` than the pool actually requests in the callback. The intended cleanup is `refundETH()`, but that is a separate, optional call.

The callback settlement path is: [2](#0-1) 

The payer stored in transient context is always `msg.sender` of the outer call: [3](#0-2) 

But when `nativeBalance >= value`, `pay` wraps and transfers ETH **without pulling anything from `payer`**. The payer identity stored in transient storage is irrelevant — the payment comes entirely from the contract's accumulated ETH balance.

---

### Impact Explanation

**Victim**: User A calls `addLiquidityExactShares` with `msg.value = 2X` for a WETH pool that only needs `X` WETH. The callback wraps `X` ETH and sends it to the pool. `X` ETH remains on the contract. If User A omits `refundETH()` (or the multicall batch doesn't include it), the `X` ETH is stranded.

**Attacker**: Calls `addLiquidityExactShares` with `msg.value = 0` for any WETH pool requesting `X` WETH. `pay` reads `address(this).balance = X`, satisfies `nativeBalance >= value`, wraps `X` ETH, and sends it to the pool — without pulling a single token from the attacker. The attacker receives a fully-funded liquidity position at User A's expense.

The `maxAmountToken0`/`maxAmountToken1` caps only bound what the pool may request; they do not prevent the router from consuming stranded ETH on behalf of a different payer. [4](#0-3) 

Position ownership (`owner`, `salt`) is fully attacker-controlled: [5](#0-4) 

So the attacker mints the position to themselves, paid for by the victim's ETH.

---

### Likelihood Explanation

Overpayment is the normal usage pattern: callers set `msg.value = maxAmountToken0` as a safety cap, and the pool requests only what it needs. The difference is stranded unless `refundETH()` is explicitly included in the same multicall batch. Forgetting this step — or calling `addLiquidityExactShares` directly without multicall — is a realistic user error. An attacker can monitor the mempool or the contract's ETH balance and front-run or follow-up immediately.

---

### Recommendation

Track how much of `address(this).balance` was contributed by the **current** call's `msg.value` and limit `pay` to spending only that amount. One approach: snapshot `address(this).balance - msg.value` at entry and treat only the delta as available for the current payer. Alternatively, require that any native ETH used in `pay` is accounted for by the current `msg.value` explicitly, and revert if the contract's pre-existing balance would be consumed.

---

### Proof of Concept

```
// Setup: WETH pool exists; token0 = WETH
// Step 1 – Victim overpays
vm.deal(victim, 2 ether);
vm.prank(victim);
adder.addLiquidityExactShares{value: 2 ether}(
    pool, victim, 0, deltas, 2 ether, 0, ""
);
// Pool requested 1 ether WETH; 1 ether ETH remains on adder
assertEq(address(adder).balance, 1 ether);

// Step 2 – Attacker calls with msg.value = 0
vm.prank(attacker);
adder.addLiquidityExactShares{value: 0}(
    pool, attacker, 0, deltas, 1 ether, 0, ""
);
// pay() sees address(this).balance = 1 ether >= 1 ether (requested)
// Wraps 1 ether ETH → WETH, sends to pool; attacker gets position
assertEq(address(adder).balance, 0);
// Attacker holds liquidity position; victim's 1 ETH is gone
``` [6](#0-5)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-88)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
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
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-178)
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
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
    _clearPayContext();
```
