The vulnerability claim is real. Let me trace through the exact code path.

**`pay()` ETH branch — no ownership tracking:**

The vulnerability is real and exploitable. Here is the full finding.

---

### Title
Unowned ETH on `MetricOmmPoolLiquidityAdder` is consumed by any subsequent WETH liquidity caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` — the **entire contract ETH balance** — as a shared pool when paying WETH. It does not track which user deposited which ETH. Any ETH left on the contract from a prior user's overpayment is silently consumed by the next caller's liquidity add, causing the prior user to lose principal and the attacker to receive free liquidity.

---

### Finding Description

`pay()` contains three branches for WETH payments: [1](#0-0) 

The branch at line 75 fires whenever `address(this).balance >= value`. It wraps exactly `value` ETH from the contract's balance and transfers it to the pool — with no check that the ETH was deposited by the current `payer`. The `payer` address (LP-B) is read from transient storage: [2](#0-1) 

but it is only used to enforce the `maxAmount` cap and to fall back to `safeTransferFrom` when the contract has no ETH. When the contract **does** have ETH (from a prior user), LP-B's WETH allowance is never touched.

The `PayContextAlreadyActive` guard uses transient storage: [3](#0-2) 

Transient storage resets at the end of every transaction, so this guard prevents reentrancy within one transaction but provides **zero protection** against a second, independent transaction exploiting leftover ETH.

`refundETH()` exists but must be called explicitly: [4](#0-3) 

If a user overpays and does not include `refundETH()` in the same `multicall`, the excess ETH is stranded on the contract and is immediately available to any subsequent caller.

---

### Impact Explanation

Direct loss of user principal. LP-A's overpaid ETH is transferred to the pool as LP-B's liquidity deposit. LP-A receives no position for the stolen ETH; LP-B receives a fully funded liquidity position without spending any of their own WETH or ETH. The loss is bounded only by how much ETH LP-A overpaid.

---

### Likelihood Explanation

Overpaying ETH is standard practice (users send a buffer to avoid reverts on uncertain pool amounts). The `addLiquidityWeighted` flow in particular performs a probe-then-pay pattern where the exact ETH needed is not known until the probe returns, making overpayment common. An attacker can monitor the contract's ETH balance on-chain and front-run or immediately follow any transaction that leaves a non-zero balance.

---

### Recommendation

Track per-call ETH deposits separately from the shared contract balance. The simplest fix is to record `msg.value` in transient storage alongside the payer context in `_setPayContext`, and in `pay()` consume only up to that recorded amount rather than `address(this).balance`. Alternatively, enforce that `refundETH()` is always called atomically (e.g., require it at the end of `_addLiquidity`), but the transient-accounting approach is more robust.

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_staleEthStolenByLPB() public {
    // LP-A adds liquidity to a WETH/TOKEN pool, overpays by 2 ETH
    // Pool needs 3 ETH; LP-A sends 5 ETH
    vm.deal(lpA, 5 ether);
    vm.prank(lpA);
    adder.addLiquidityExactShares{value: 5 ether}(
        pool, salt, deltas, 5 ether, 0, ""
    );
    // LP-A forgot refundETH(); 2 ETH remains on adder
    assertEq(address(adder).balance, 2 ether);

    // LP-B calls with 0 ETH, WETH pool needs 2 ETH
    // pay(WETH, lpB, pool, 2e18) fires nativeBalance(2e18) >= value(2e18) branch
    // LP-A's 2 ETH is wrapped and sent to pool for LP-B's position
    vm.prank(lpB);
    adder.addLiquidityExactShares{value: 0}(
        pool, salt, deltasB, 2 ether, 0, ""
    );

    assertEq(address(adder).balance, 0);          // LP-A's ETH is gone
    // LP-B holds a valid liquidity position funded entirely by LP-A's ETH
}
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L291-296)
```text
    if (_tloadAddress(T_SLOT_PAY_POOL) != address(0)) revert PayContextAlreadyActive();
    _tstoreAddress(T_SLOT_PAY_POOL, pool);
    _tstoreAddress(T_SLOT_PAY_PAYER, payer);
    _tstore(T_SLOT_PAY_MAX0, maxAmountToken0);
    _tstore(T_SLOT_PAY_MAX1, maxAmountToken1);
  }
```
