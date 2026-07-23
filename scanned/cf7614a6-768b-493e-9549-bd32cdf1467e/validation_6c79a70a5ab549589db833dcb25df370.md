### Title
Router `pay()` WETH branch silently drains any stranded native ETH, enabling theft of prior users' funds — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments.sol` settles WETH obligations by first consuming the router's **entire** native ETH balance, regardless of which user deposited it. Because every payable entry-point (`exactInputSingle`, `exactOutputSingle`, `multicall`, etc.) can leave excess `msg.value` on the router with no automatic refund, any subsequent caller whose swap token is WETH will have their payment obligation silently satisfied with a prior user's stranded ETH. The prior user loses that ETH permanently.

---

### Finding Description

`pay()` contains three branches for the WETH case:

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
``` [1](#0-0) 

`address(this).balance` is a **shared, unattributed pool**. It accumulates from:

1. Any `msg.value` sent with a payable swap call where the pool's callback requests less than the full `msg.value` (e.g., a price-limit partial fill).
2. Any `multicall{value: X}` where the user forgets to append `refundETH()`.
3. Any direct ETH send from WETH's `withdraw()` that overshoots a prior unwrap.

None of the swap entry-points (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`) automatically refund leftover ETH after the callback settles. [2](#0-1) 

When a subsequent caller swaps with `tokenIn = WETH`, the callback fires `_justPayCallback` → `pay(WETH, currentPayer, pool, amount)`. If `address(this).balance >= amount`, the router wraps and forwards the stranded ETH **without touching `currentPayer`'s wallet at all**. The attacker pays nothing; the victim's ETH is gone. [3](#0-2) 

The structural analog to the LightClient `force()` bug is exact: just as `force()` uses the "best available" sync-committee update (even with minimal signatures) when the normal finalization threshold cannot be met, `pay()` uses the "best available" ETH source (the router's entire native balance, from any prior user) when the normal `safeTransferFrom(payer, …)` path would be the correct settlement. In both cases a fallback path that consumes whatever is available bypasses the authorization requirement that protects honest participants.

---

### Impact Explanation

**Direct loss of user ETH — High severity.**

A victim who sends any `msg.value` with a WETH swap and receives less than full fill (or simply over-sends) loses the residual ETH to the next WETH swapper in the same block. The attacker needs zero ETH or WETH of their own; they only need to observe the router's balance (trivially readable on-chain) and submit a WETH swap for exactly that amount before the victim calls `refundETH()`.

---

### Likelihood Explanation

**Medium.** ETH stranding is a normal user pattern:

- Frontends commonly send `msg.value = amountIn` for WETH swaps; any partial fill (price-limit hit) leaves a residual.
- `multicall` users frequently omit `refundETH()` as a trailing step.
- MEV bots monitoring `address(router).balance` can front-run the victim's `refundETH()` call in the same block.

No privileged role, malicious token, or non-standard ERC-20 is required.

---

### Recommendation

Track the ETH that belongs to the **current transaction** rather than the contract's global balance. Two concrete options:

1. **Snapshot `msg.value` at entry and pass it through to `pay()`**: replace `address(this).balance` with a locally captured `uint256 ethBudget = msg.value` stored in transient storage alongside the callback context, and consume only up to that budget.
2. **Refund automatically**: after every swap entry-point clears the callback context, call `refundETH()` unconditionally so no ETH can persist across transactions.

---

### Proof of Concept

```
// Step 1 – Alice strands ETH on the router
// Alice calls exactInputSingle{value: 1 ether} with:
//   tokenIn  = WETH
//   amountIn = 1 ether
//   priceLimitX64 = <tight limit that causes a partial fill>
// Pool only consumes 0.5 ether; callback fires pay(WETH, Alice, pool, 0.5 ether).
// nativeBalance = 1 ether >= 0.5 ether → router wraps 0.5 ether, sends to pool.
// 0.5 ether remains on the router. Alice's swap completes; she never calls refundETH().

// Step 2 – Bob steals Alice's stranded ETH
// Bob calls exactInputSingle (msg.value = 0) with:
//   tokenIn  = WETH
//   amountIn = 0.5 ether
// Pool calls metricOmmSwapCallback → _justPayCallback → pay(WETH, Bob, pool, 0.5 ether).
// nativeBalance = 0.5 ether >= 0.5 ether → router wraps Alice's 0.5 ether, sends to pool.
// Bob's swap settles in full; Bob paid 0 ETH and 0 WETH from his own wallet.
// Alice's 0.5 ether is permanently lost.
``` [4](#0-3) [2](#0-1)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-199)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
  }
```
