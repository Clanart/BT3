### Title
`pay()` uses entire router native ETH balance, allowing stranded ETH from prior transactions to subsidize subsequent WETH swaps — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` function in `PeripheryPayments.sol` reads `address(this).balance` — the router's **aggregate** native ETH balance — when settling WETH payments. Because this balance is not scoped to the current caller's transaction, any ETH left on the router by a prior user is silently consumed to subsidize the next WETH swap, causing direct loss of the prior user's principal.

---

### Finding Description

The external bug class is: *a payment amount is derived from the contract's live balance rather than a fixed, per-call value, so the wrong amount is sent when the balance deviates from the expected level.*

The native analog lives in `pay()`: [1](#0-0) 

When `token == WETH` and `payer != address(this)`, the function branches on `address(this).balance` (line 74). If `0 < nativeBalance < value`, it wraps **all** available native ETH and sends it to the pool, then pulls only the remainder from the payer via `safeTransferFrom`: [2](#0-1) 

The router holds **no per-user ETH accounting**. `address(this).balance` reflects the aggregate of all ETH sent by all callers across all prior transactions that was not yet refunded. A user who sends excess ETH — e.g., calls `exactInputSingle{value: 1 ETH}(amountIn=0.5 ETH WETH)` — leaves 0.5 ETH on the router after `pay()` wraps only the required 0.5 ETH: [3](#0-2) 

The next caller who executes any WETH swap will have that 0.5 ETH silently applied toward their payment, consuming the prior user's funds. The entry points that expose this are all payable: [4](#0-3) [5](#0-4) [6](#0-5) 

The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does not prevent `msg.value` accumulation across payable calls: [7](#0-6) 

---

### Impact Explanation

**Direct loss of user principal.** The prior user's stranded ETH is consumed by a subsequent user's swap. The prior user loses their ETH (up to 100 % of the stranded amount) and the subsequent user receives a proportional discount on their WETH payment. The loss is bounded only by the amount of ETH stranded on the router at the time of the next WETH swap. This satisfies the contest's Critical/High/Medium direct-loss-of-principal gate.

---

### Likelihood Explanation

**Medium.** Two realistic triggers exist:

1. **Accidental**: A user calls a payable swap entry point with more ETH than the swap requires and omits `refundETH()` from their multicall (or calls the function directly, not via multicall). The excess ETH is immediately at risk in the next block.
2. **Adversarial front-run**: A victim broadcasts a `refundETH()` transaction to reclaim stranded ETH. An attacker observing the mempool front-runs it with a WETH swap that has `amountIn` equal to the stranded amount, consuming the victim's ETH while paying less WETH from their own account.

Both triggers are unprivileged and require no special setup beyond a WETH approval on the router.

---

### Recommendation

Track the ETH available for the current transaction using transient storage. At the start of each payable entry point, store `msg.value` in a transient slot and decrement it as ETH is consumed inside `pay()`. Replace the `address(this).balance` read with the transient per-call budget so that only ETH explicitly sent in the current call can be used to wrap WETH.

---

### Proof of Concept

```
1. User A calls exactInputSingle{value: 1 ETH}(
       pool, tokenIn=WETH, amountIn=0.5 ETH, ...
   )
   → pay(WETH, UserA, pool, 0.5 ETH) is called
   → nativeBalance = 1 ETH ≥ 0.5 ETH → wraps 0.5 ETH, sends to pool
   → 0.5 ETH remains on the router (User A does not call refundETH)

2. Attacker calls exactInputSingle(
       pool, tokenIn=WETH, amountIn=1 ETH, ...
   )  ← no msg.value, but has 0.5 WETH approved
   → pay(WETH, Attacker, pool, 1 ETH) is called
   → nativeBalance = 0.5 ETH  (0 < 0.5 < 1)
   → wraps 0.5 ETH (User A's funds), sends to pool
   → safeTransferFrom(Attacker, pool, 0.5 WETH)
   → Attacker pays only 0.5 WETH for a 1 ETH WETH swap

Result: User A loses 0.5 ETH; Attacker receives a 50 % discount.
```

The corrupted value is `nativeBalance` at line 74 of `PeripheryPayments.sol`: it reads the router's global ETH balance instead of the ETH budget belonging to the current caller, exactly mirroring the external bug's pattern of deriving a payment from the contract's live balance rather than a fixed per-call amount. [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
