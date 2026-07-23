### Title
Router-held native ETH is consumed by any subsequent WETH-input swap or `refundETH` call, enabling cross-user ETH theft — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay` function in `PeripheryPayments.sol` uses the router's total `address(this).balance` — without any per-user attribution — to cover WETH payments during swap callbacks. Separately, `refundETH()` transfers the router's entire native ETH balance to `msg.sender` with no ownership check. Any native ETH stranded on the router by one user (e.g., by sending `msg.value` larger than `amountIn` without a `refundETH` step) is immediately claimable by any other user in a subsequent transaction.

---

### Finding Description

**Root cause — `pay` function, lines 73–84:** [1](#0-0) 

When `token == WETH` and `payer != address(this)`, the function reads `address(this).balance` — the router's *total* native ETH balance — and uses it to deposit WETH on behalf of the current payer. There is no record of which user contributed which portion of that balance. If the router holds residual ETH from a prior user's `msg.value`, that ETH is silently consumed to settle the current user's WETH obligation, and the current user's own WETH allowance is never touched.

**Root cause — `refundETH`, lines 58–63:** [2](#0-1) 

`refundETH()` is `external payable` with no access control. It transfers the router's *entire* native ETH balance to `msg.sender`. Any ETH left on the router by a prior user is fully claimable by any caller in a later transaction.

**How ETH becomes stranded:**

Every swap entry-point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) is `payable`. [3](#0-2) 

A user who calls `exactInputSingle{value: X}(amountIn: Y)` where `Y < X` will have `X - Y` ETH stranded on the router after the swap completes, because there is no automatic refund and the function does not enforce `msg.value == amountIn`.

**Analog to the external bug class:**

The external report shows that `lastUpdateTime` — a shared state variable — is updated by any caller to a value that extends `periodFinish`, allowing late claimers to consume rewards that belong to earlier depositors. The structural analog here is `address(this).balance`: it is a shared, unattributed pool of value that any subsequent caller's WETH payment (via `pay`) or direct `refundETH` call can consume in full, regardless of which user deposited it.

---

### Impact Explanation

**Direct loss of user principal.** A user who sends `msg.value > amountIn` without a `refundETH` step in the same `multicall` loses the excess ETH permanently to the next caller who either:

1. Calls `refundETH()` directly (steals the full stranded balance as native ETH), or
2. Calls any WETH-input swap function with no `msg.value` — the `pay` function silently uses the stranded ETH to cover their WETH obligation, so the attacker pays zero WETH for a real swap.

The loss is bounded only by the amount the victim over-sent. For large swaps with loose `msg.value` estimates this can be substantial.

---

### Likelihood Explanation

- `exactInputSingle` and all other swap functions are `payable`, so users routinely send ETH directly (not only via `multicall`).
- The `refundETH` step is opt-in and not enforced by the router; integrators or users unfamiliar with the Uniswap v3 multicall pattern will omit it.
- The attack requires no special privilege: any EOA can call `refundETH()` or submit a WETH-input swap in the next block after the victim's transaction.
- The router's ETH balance is publicly readable on-chain, making victim detection trivial.

---

### Recommendation

1. **Track per-transaction ETH budget.** Store `msg.value` in transient storage at the start of each top-level call and deduct from it inside `pay`. Revert or refund automatically if the budget is exceeded or unused.
2. **Alternatively, enforce `msg.value == 0` for non-WETH paths** and require WETH-input callers to use `multicall{value}` with an explicit `refundETH` step, and document this as a hard invariant.
3. **`refundETH` attribution.** If the router must hold ETH across calls, gate `refundETH` to return only the amount the current `msg.sender` deposited in the current transaction (tracked via transient storage), not the entire contract balance.

---

### Proof of Concept

```
// Step 1 — Victim strands ETH
// Alice calls exactInputSingle{value: 1 ether}(amountIn: 0.5 ether, tokenIn: WETH, ...)
// The pay() callback uses 0.5 ETH from address(this).balance to deposit WETH.
// 0.5 ETH remains on the router. Alice's tx ends; no refundETH was included.

// Step 2a — Attacker steals via refundETH (simplest path)
// Bob calls router.refundETH() in the next transaction.
// refundETH() sends address(this).balance (0.5 ETH) to Bob.
// Alice loses 0.5 ETH; Bob gains 0.5 ETH at zero cost.

// Step 2b — Attacker steals via free WETH swap
// Bob calls exactInputSingle(amountIn: 0.5 ether, tokenIn: WETH, ...)
//   with msg.value = 0.
// pay(WETH, Bob, pool, 0.5 ether) sees nativeBalance = 0.5 ETH >= 0.5 ETH.
// Router deposits Alice's 0.5 ETH as WETH and sends it to the pool.
// Bob's WETH allowance is never touched; Bob receives the swap output for free.
``` [4](#0-3) [2](#0-1) [3](#0-2)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
