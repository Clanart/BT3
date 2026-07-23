### Title
`pay()` Consumes Any Router-Held Native ETH to Settle WETH Obligations Regardless of Payer Identity, Enabling Cross-User ETH Theft — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` (the router's entire native ETH balance) to cover WETH swap payments before pulling from the recorded `payer`. Because the router's ETH balance is not attributed to any specific user, any ETH stranded on the router from a prior user's transaction can be silently consumed to settle a completely different user's WETH obligation, causing direct loss of the original depositor's ETH.

---

### Finding Description

`PeripheryPayments.pay()` contains the following WETH branch:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);          // payer never charged
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

When `nativeBalance >= value`, the function deposits and forwards the router's native ETH to the pool and **never calls `safeTransferFrom(payer, …)`**. The `payer` identity stored in transient storage is completely ignored. Any native ETH sitting on the router — regardless of which user deposited it or when — is consumed first.

ETH becomes stranded on the router in the normal, documented usage pattern:

- A user calls `exactOutputSingle` (or `exactOutput`) with WETH as `tokenIn` and sends `msg.value = amountInMaximum`. The pool requests only the actual `amountIn < amountInMaximum` in the callback; `pay()` deposits exactly `amountIn` and leaves `amountInMaximum − amountIn` as native ETH on the router.
- The user is expected to follow up with `refundETH()` in the same multicall. If they omit it, or if the multicall is constructed without it, the surplus ETH persists across transactions.

An attacker who observes stranded ETH on the router can then call `exactInputSingle` (or `exactInput`) with WETH as `tokenIn`, `msg.value = 0`, and `amountIn` equal to the stranded amount. When the pool fires the swap callback, `_justPayCallback` calls `pay()` with the attacker as `payer`. Because `nativeBalance >= value`, the router uses the victim's ETH to settle the obligation and the attacker's `payer` address is never charged. The attacker receives the full swap output at zero cost.

The same path is reachable through `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback`, which also calls `pay(token0/token1, payer, …)` — if either pool token is WETH, the same substitution occurs.

---

### Impact Explanation

**Critical / High — direct loss of user principal.**

The victim loses the full stranded ETH amount (up to `amountInMaximum − amountIn` per exact-output swap). The attacker receives the corresponding swap output tokens at zero cost. There is no slippage or oracle condition required; the loss is exact and deterministic. The attack is repeatable for every stranded-ETH event and requires no special role or privilege.

---

### Likelihood Explanation

**Medium.**

The precondition — ETH stranded on the router — arises naturally from the documented `exactOutputSingle + refundETH` multicall pattern whenever a user omits the `refundETH` step, sends excess `msg.value`, or has a partial multicall revert after ETH is deposited. Attackers can monitor the router's ETH balance on-chain and execute the theft in the next block with a single `exactInputSingle` call carrying `msg.value = 0`.

---

### Recommendation

Remove the native-ETH shortcut from the external-payer branch. When `payer != address(this)`, always pull WETH directly from the payer:

```solidity
} else if (token == WETH) {
    if (payer == address(this)) {
        // mid-path: router already holds WETH, transfer it
        IERC20(WETH).safeTransfer(recipient, value);
    } else {
        // first hop: pull from the external payer; native ETH is irrelevant here
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

If native-ETH input must be supported, gate it explicitly on `msg.value > 0` within the same call frame (e.g., deposit `msg.value` to WETH at the top of `exactInputSingle`/`exactOutputSingle` before setting the callback context, then treat the payer as `address(this)` for the WETH leg). This ensures the native ETH is attributed to the current caller before any callback fires.

---

### Proof of Concept

```
Setup:
  - Router deployed with WETH address.
  - Pool(WETH, TOKEN) exists with liquidity.
  - Victim has 1 ETH.

Step 1 — Victim strands ETH:
  victim.exactOutputSingle{value: 1 ether}(
      pool=Pool(WETH,TOKEN), tokenIn=WETH, tokenOut=TOKEN,
      amountOut=X, amountInMaximum=1 ether, recipient=victim
  )
  // Pool requests amountIn = 0.6 ETH in callback.
  // pay() deposits 0.6 ETH as WETH, transfers to pool.
  // 0.4 ETH remains on router. Victim omits refundETH().

Step 2 — Attacker steals:
  attacker.exactInputSingle{value: 0}(
      pool=Pool(WETH,TOKEN), tokenIn=WETH, tokenOut=TOKEN,
      amountIn=0.4 ETH, amountOutMinimum=0, recipient=attacker
  )
  // Pool fires callback requesting 0.4 ETH worth of WETH.
  // pay() sees address(this).balance = 0.4 ETH >= 0.4 ETH.
  // Deposits victim's 0.4 ETH as WETH, transfers to pool.
  // Attacker's payer address is never charged.
  // Attacker receives TOKEN output; victim loses 0.4 ETH.

Assert:
  attacker WETH/ETH spent: 0
  victim ETH lost: 0.4 ETH (the stranded surplus)
  router ETH balance after: 0
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
