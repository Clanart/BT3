The `pay` function in `PeripheryPayments.sol` is the key code to analyze. Let me trace the exact execution path.

### Title
Residual ETH on Router Consumed by Subsequent WETH Swaps, Causing Direct Fund Loss for Prior ETH Depositors — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay` function's partial-ETH branch reads `address(this).balance` globally, with no per-transaction accounting. Any ETH left on the router from a prior transaction (e.g., a user who sent excess `msg.value` without calling `refundETH()`) is silently consumed by the next WETH swap, reducing the WETH pulled from that swapper's allowance. The original ETH depositor loses their funds with no recourse.

---

### Finding Description

In `PeripheryPayments.pay`, when `token == WETH` and `payer != address(this)`, the function reads the router's entire native ETH balance: [1](#0-0) 

The three branches are:

| Condition | Behavior |
|---|---|
| `nativeBalance >= value` | Wraps exactly `value` ETH, transfers WETH to pool. Excess ETH stays on router. |
| `0 < nativeBalance < value` | Wraps all `nativeBalance` ETH, transfers that WETH, then pulls `value - nativeBalance` WETH from payer. |
| `nativeBalance == 0` | Pulls full `value` WETH from payer. |

The middle branch is the vulnerable one. `address(this).balance` is the **total** ETH balance of the router — it includes ETH from any prior transaction that was not refunded. There is no per-transaction ETH accounting.

**How ETH becomes stranded on the router:**

The `receive()` guard only blocks direct ETH transfers from non-WETH addresses: [2](#0-1) 

However, ETH sent as `msg.value` in a payable call (e.g., `exactInputSingle{value: X}`) is credited to the contract's balance **without** triggering `receive()`. If a user sends excess ETH and omits `refundETH()`, the surplus remains on the router indefinitely. The test suite itself demonstrates this pattern — `refundETH()` is always a separate, optional multicall step: [3](#0-2) 

---

### Impact Explanation

**Concrete attack scenario:**

1. User A calls `exactInputSingle{value: 3 ETH}` with `amountIn = 2 ETH` (WETH as tokenIn), omitting `refundETH()`. After the swap, 1 ETH remains on the router.
2. Attacker calls `exactInputSingle` with WETH as tokenIn, `amountIn = 2 ETH`.
3. Inside the callback, `pay(WETH, attacker, pool, 2e18)` is called. `nativeBalance = 1 ETH`.
4. The partial branch executes:
   - `IWETH9.deposit{value: 1 ETH}()` — wraps User A's stranded ETH.
   - `safeTransfer(pool, 1 WETH)` — sends it to the pool.
   - `safeTransferFrom(attacker, pool, 1 WETH)` — pulls only 1 WETH from attacker.
5. Pool receives the correct 2 WETH for the attacker's swap.
6. **User A loses 1 ETH. Attacker pays 1 WETH instead of 2 WETH.**

The pool's invariant is satisfied (it receives the correct input), so no pool-level check catches this. The loss is entirely borne by User A.

---

### Likelihood Explanation

- Sending excess ETH without `refundETH()` is a realistic user error, especially for EOA callers who do not use multicall.
- The `exactInputSingle`, `exactOutputSingle`, `exactInput`, and `exactOutput` functions are all `payable` and accept arbitrary `msg.value` with no enforcement that `msg.value == amountIn`.
- An attacker can monitor the mempool or on-chain state for any non-zero `address(router).balance` and immediately execute a WETH swap to drain it.
- The `receive()` guard does **not** prevent this — it only blocks direct ETH pushes, not the consumption of already-stranded ETH.

---

### Recommendation

Track the ETH that belongs to the current transaction separately from any pre-existing balance. The simplest fix is to record `address(this).balance` at the start of each swap entry point (before the pool call) and pass that snapshot into `pay`, rather than re-reading `address(this).balance` inside the callback. Alternatively, enforce that `msg.value` is consumed exactly (revert if `address(this).balance` after the swap is non-zero and no `refundETH()` was called), or automatically refund excess ETH at the end of each swap function.

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_strandedEthStolenByWethSwap() public {
    // Step 1: User A sends 3 ETH for a 2 ETH swap, forgets refundETH()
    vm.prank(userA);
    router.exactInputSingle{value: 3 ether}(
        ExactInputSingleParams({
            tokenIn: address(weth), amountIn: 2 ether, /* ... */
        })
    );
    // 1 ETH is now stranded on the router
    assertEq(address(router).balance, 1 ether);

    // Step 2: Attacker executes a 2 WETH swap with no ETH sent
    uint256 attackerWethBefore = weth.balanceOf(attacker);
    vm.prank(attacker);
    router.exactInputSingle{value: 0}(
        ExactInputSingleParams({
            tokenIn: address(weth), amountIn: 2 ether, /* ... */
        })
    );

    // Attacker only spent 1 WETH, not 2
    assertEq(attackerWethBefore - weth.balanceOf(attacker), 1 ether);
    // Router ETH is now 0 — User A's ETH was consumed
    assertEq(address(router).balance, 0);
}
```

The pool receives the full 2 WETH, so no pool-side check reverts. The loss is silent and undetectable from the pool's perspective.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
  }
```
