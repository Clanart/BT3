The `pay()` function in `PeripheryPayments.sol` is the critical code to examine. Let me read it alongside the callback flow in the liquidity adder.

The `pay()` function is the critical code. The vulnerability claim is well-formed and traces correctly through the code. Let me verify the exact logic:

**`pay()` at lines 73–84:**
```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← total contract ETH, not just msg.value
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance); // ← only pulls remainder
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

The function reads `address(this).balance` — the **total** ETH held by the contract — not just the `msg.value` of the current call. Any ETH left over from a prior transaction is indistinguishable from ETH sent in the current call.

**How residual ETH accumulates:**

The `receive()` guard blocks direct ETH transfers from non-WETH addresses. However, all three entry points are `payable`:
- `addLiquidityExactShares` (lines 56–68, 71–81)
- `addLiquidityWeighted` (lines 88–116, 123–148)
- `multicall` (lines 42–47)

If a user calls any of these with `msg.value > 0` but the pool's WETH leg is not needed (e.g., liquidity is entirely in token1 bins), `pay()` is called for token1 only — the ETH is never consumed and stays in the contract. If the user omits `refundETH`, that ETH is permanently stranded until someone else's `pay()` call consumes it.

**The exploit path:**

1. User A calls `addLiquidityExactShares{value: 0.5 ETH}(...)` on a pool where only token1 is needed. `pay()` is called for token1 (not WETH), so the 0.5 ETH is never wrapped. User A forgets `refundETH`. The adder now holds 0.5 ETH.
2. User B calls `addLiquidityExactShares` (no ETH sent) with WETH as token0 and `value = 1e18`.
3. In `pay()`: `nativeBalance = 0.5e18 > 0` but `< 1e18`, so the `else if` branch fires:
   - Wraps 0.5 ETH → transfers 0.5e18 WETH to pool
   - Pulls only `1e18 - 0.5e18 = 0.5e18` WETH from User B via `transferFrom`
4. Pool receives correct 1e18 WETH. User B's net cost is 0.5e18 WETH instead of 1e18. User A's 0.5 ETH is consumed and unrecoverable.

**Does the `receive()` guard prevent this?**

No. The guard only blocks `address.call{value}("")` style transfers. It does not block ETH arriving via `msg.value` in payable function calls. Residual ETH from legitimate-but-incomplete multicalls is the realistic source.

**Can an attacker deliberately profit?**

An attacker cannot profit by self-seeding (they'd spend 0.5 ETH to save 0.5 ETH — net zero). The real victim is an innocent user who accidentally leaves ETH in the contract. The attacker exploits the window before the victim calls `refundETH`.

---

### Title
`PeripheryPayments.pay()` consumes residual contract ETH from prior transactions to subsidize the current payer's WETH obligation — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`pay()` uses `address(this).balance` to determine how much ETH to wrap toward a WETH payment. This includes ETH left over from prior payable calls, not just the current `msg.value`. A subsequent caller paying WETH can have their `transferFrom` obligation reduced by the residual ETH, causing the prior depositor to permanently lose those funds.

### Finding Description
In `PeripheryPayments.pay()`, the mixed-payment branch for WETH reads `address(this).balance` at line 74: [1](#0-0) 

This balance is the **total** ETH held by the contract, not the ETH sent in the current transaction. ETH can accumulate in the contract when a user calls a `payable` entry point (`addLiquidityExactShares`, `addLiquidityWeighted`, `multicall`) with `msg.value > 0` but the pool's WETH leg is not required for that call (e.g., liquidity is entirely in token1 bins). If the user omits `refundETH`, the ETH is stranded. [2](#0-1) 

When a subsequent caller invokes `addLiquidityExactShares` with WETH as the payment token, `pay()` wraps the residual ETH first and only pulls `value - residualETH` from the payer via `safeTransferFrom`. The pool receives the correct total, but the payer's net cost is reduced by the residual ETH amount — which is permanently consumed from the prior depositor's balance. [3](#0-2) 

### Impact Explanation
The prior user who left ETH in the contract loses those funds permanently — they cannot be recovered via `refundETH` once consumed. The current payer pays less than the required token amount. This is a direct principal loss for the prior depositor. The pool itself is not insolvent (it receives the correct WETH), but the payment invariant — "the registered payer must supply the full token amount" — is violated.

### Likelihood Explanation
The `receive()` guard prevents deliberate ETH injection by attackers, so the precondition requires an innocent user to accidentally leave ETH in the contract. This is realistic: the `multicall` + `refundETH` pattern is easy to omit, and any call to a `payable` liquidity function on a pool where only token1 is needed will silently strand the sent ETH. An attacker monitoring the contract's ETH balance can exploit the window before the victim calls `refundETH`.

### Recommendation
Track only the ETH sent in the **current** transaction. Replace `address(this).balance` with a locally captured `msg.value` passed into `pay()`, or snapshot the balance at the top of the outermost payable entry point and pass it through the call chain. This ensures residual ETH from prior transactions is never consumed on behalf of a new payer.

### Proof of Concept
1. Deploy `MetricOmmPoolLiquidityAdder` with a WETH/token1 pool.
2. Call `addLiquidityExactShares{value: 0.5 ether}(pool, owner, salt, token1OnlyDelta, max0, max1, "")` — pool only needs token1, so `pay()` is called for token1 only; 0.5 ETH stays in the adder.
3. From a second account (User B), call `addLiquidityExactShares(pool, owner2, salt2, wethDelta, 1e18, max1, "")` with no ETH sent.
4. Observe: `pay()` wraps the 0.5 ETH residual and pulls only 0.5e18 WETH from User B via `transferFrom`.
5. Assert: User B's WETH allowance consumed = 0.5e18 (not 1e18). User A's 0.5 ETH is gone and `refundETH` returns 0.

### Citations

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
