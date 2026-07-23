The code confirms the vulnerability. Key observations:

1. `pay()` at line 74-77 uses `address(this).balance` with no per-caller accounting — any ETH on the contract is consumed for the current call's WETH payment.
2. `receive()` at line 32-34 only blocks *direct* ETH transfers from non-WETH addresses; it does NOT block ETH sent via `msg.value` in `payable` function calls like `addLiquidityExactShares{value: X}(...)`.
3. `refundETH()` is not called atomically at the end of `_addLiquidity` — it is opt-in via `multicall`.
4. Transient storage resets per-transaction, so the `PayContextAlreadyActive` guard provides zero cross-transaction protection.

---

Audit Report

## Title
Unowned ETH on `MetricOmmPoolLiquidityAdder` is consumed by any subsequent WETH liquidity caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` uses `address(this).balance` as the ETH source for WETH payments without tracking which caller deposited which ETH. If a user overpays ETH and does not call `refundETH()` in the same multicall, the stranded ETH is silently consumed by the next caller's WETH liquidity deposit, causing the prior user to lose principal and the subsequent caller to receive a fully funded liquidity position at no cost.

## Finding Description
`pay()` contains three branches for WETH payments:

```solidity
// PeripheryPayments.sol L73-84
} else if (token == WETH) {
  uint256 nativeBalance = address(this).balance;
  if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
  } else if (nativeBalance > 0) { ... }
  else { IERC20(WETH).safeTransferFrom(payer, recipient, value); }
}
```

When `nativeBalance >= value`, the function wraps exactly `value` ETH from the contract's total balance and transfers it to the pool. The `payer` address loaded from transient storage is never consulted in this branch — it is only used in the `safeTransferFrom` fallback paths. There is no per-caller ETH accounting.

ETH reaches the contract legitimately via `msg.value` on `payable` functions (`addLiquidityExactShares`, `addLiquidityWeighted`). The `receive()` guard (L32-34) only blocks plain ETH transfers with no calldata; it does not prevent ETH from being deposited via `msg.value` in function calls.

`_addLiquidity` (L183-207) does not call `refundETH()` after settlement. `refundETH()` (L58-63) must be explicitly included by the user in a `multicall`. If omitted, excess ETH is stranded on the contract indefinitely.

The `PayContextAlreadyActive` guard uses transient storage (L291), which resets at the end of every transaction. It prevents reentrancy within one transaction but provides zero protection against a second independent transaction exploiting leftover ETH.

Exploit path:
1. LP-A calls `addLiquidityExactShares{value: 5 ether}(...)` for a WETH/TOKEN pool needing 3 ETH. `pay()` wraps 3 ETH; 2 ETH remains on the contract.
2. LP-A does not include `refundETH()` in the multicall (or calls the non-multicall entry point directly).
3. LP-B calls `addLiquidityExactShares{value: 0}(...)` for a WETH pool needing 2 ETH. `pay()` sees `nativeBalance (2e18) >= value (2e18)`, wraps LP-A's 2 ETH, and sends it to the pool credited to LP-B's position.
4. LP-A's 2 ETH is gone; LP-B holds a valid liquidity position funded entirely by LP-A's ETH.

## Impact Explanation
Direct loss of user principal. LP-A's overpaid ETH is transferred to the pool as LP-B's liquidity deposit. LP-A receives no position for the stolen ETH. LP-B receives a fully funded liquidity position without spending any of their own WETH or ETH. The loss is bounded only by how much ETH LP-A overpaid. This is a Critical/High direct loss of user principal meeting Sherlock contest thresholds.

## Likelihood Explanation
Overpaying ETH is standard practice — users send a buffer to avoid reverts when the exact pool amount is uncertain. The `addLiquidityWeighted` flow explicitly performs a probe-then-pay pattern where the exact ETH needed is not known until the probe returns, making overpayment structurally common. An attacker can monitor the contract's ETH balance on-chain and immediately follow any transaction that leaves a non-zero balance. No special privileges are required; any unprivileged LP can exploit this.

## Recommendation
Track per-call ETH deposits in transient storage alongside the payer context in `_setPayContext`. Record `msg.value` in a dedicated transient slot (e.g., `T_SLOT_PAY_ETH`) and in `pay()` consume only up to that recorded amount rather than `address(this).balance`. Decrement the slot as ETH is consumed. Alternatively, enforce that `refundETH()` is called atomically at the end of `_addLiquidity`, but the transient-accounting approach is more robust as it eliminates the shared-balance assumption entirely.

## Proof of Concept
```solidity
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

    // LP-B calls with 0 ETH; WETH pool needs 2 ETH
    // pay(WETH, lpB, pool, 2e18) fires nativeBalance(2e18) >= value(2e18) branch
    // LP-A's 2 ETH is wrapped and sent to pool for LP-B's position
    vm.prank(lpB);
    adder.addLiquidityExactShares{value: 0}(
        pool, salt, deltasB, 2 ether, 0, ""
    );

    assertEq(address(adder).balance, 0);
    // LP-B holds a valid liquidity position funded entirely by LP-A's ETH
}
```