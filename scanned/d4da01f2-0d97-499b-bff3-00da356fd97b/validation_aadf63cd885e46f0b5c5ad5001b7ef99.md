Looking at the code I've read, I need to synthesize my findings. Let me analyze the key files I've examined.

From `MetricOmmSimpleRouter.sol`, the `_validatePath` function only validates array lengths:

```solidity
function _validatePath(...) internal pure {
    if (
        tokens.length < 2 || pools.length != tokens.length - 1 || extensionDatas.length != pools.length
            || pools.length > MAX_PATH_POOLS
    ) {
        revert InvalidPath();
    }
}
```

No token or pool address is checked for `address(0)`. All four swap entry points (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`) pass `params.recipient` directly to the pool's `swap()` call without any zero-address guard.

---

### Title
Missing Zero-Address Recipient Guard Causes Permanent Loss of Swap Output Tokens — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

### Summary
All four swap entry points in `MetricOmmSimpleRouter` accept `address(0)` as `recipient` without validation. When a user (or an integrating contract with a bug) passes `address(0)`, the pool sends output tokens to `address(0)` and permanently burns them, while the user's input tokens are still consumed via the callback. There is no guard anywhere in the router path to prevent this.

### Finding Description

`exactInputSingle` (line 67), `exactOutputSingle` (line 130), `exactInput` (line 92), and `exactOutput` (line 154) all forward `params.recipient` directly to `IMetricOmmPoolActions.swap()`:

```solidity
// exactInputSingle — line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,   // ← no address(0) check
        params.zeroForOne,
        ...
    );
```

```solidity
// exactInput — line 104-112
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(
        i == last ? params.recipient : address(this),  // ← no address(0) check on recipient
        ...
    );
```

The `_validatePath` helper (line 235-245) only enforces array-length invariants; it performs no address-validity checks on `tokens[]`, `pools[]`, or the caller-supplied `recipient`.

The callback path (`_justPayCallback`, line 192-199) correctly pulls input tokens from the payer and sends them to `msg.sender` (the pool). The pool then transfers output tokens to `recipient`. If `recipient == address(0)`, the output is permanently burned while the input transfer succeeds — a one-sided settlement that leaves the user with nothing.

The analog to the ERC721Votes bug is exact:
- **ERC721Votes**: `delegate(address(0))` → votes transferred out of owner, credited to `address(0)` (burned), owner loses voting power.
- **MetricOmmSimpleRouter**: `exactInputSingle({recipient: address(0), ...})` → input tokens transferred out of user (via callback), output tokens sent to `address(0)` (burned), user receives nothing.

### Impact Explanation
Direct, permanent loss of the user's swap output tokens. The user pays the full `amountIn` (pulled via `_justPayCallback`) but receives zero output because the pool sends the output to `address(0)`. The loss equals the full output amount of the swap. No recovery path exists once the transaction settles.

### Likelihood Explanation
Medium. This requires `address(0)` to reach the `recipient` field. Realistic triggers include:
1. An integrating contract that has a bug in its recipient-address computation (e.g., uninitialized variable, failed address lookup returning zero).
2. A front-end that passes an unset wallet address.
3. A multicall batch where a prior call's return value is misused as the recipient.

The router is the last line of defense before the pool executes; without a guard here, any upstream mistake is unrecoverable.

### Recommendation
Add a zero-address check at the top of each swap entry point:

```solidity
// In exactInputSingle, exactOutputSingle, exactInput, exactOutput:
if (params.recipient == address(0)) revert InvalidRecipient();
```

Alternatively, add a shared internal helper `_requireValidRecipient(address recipient)` and call it from all four functions. This mirrors the pattern already used in `MetricOmmPoolLiquidityAdder._validateOwner` (line 247-249):

```solidity
function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
}
```

The liquidity adder already protects its `owner` parameter; the router must apply the same discipline to `recipient`.

### Proof of Concept

1. User calls `exactInputSingle` with `amountIn = 1000 USDC`, `tokenIn = USDC`, `recipient = address(0)` (e.g., due to an uninitialized variable in an integrating contract).
2. Router sets callback context: payer = `msg.sender`, token = USDC.
3. Pool executes swap; output ETH is transferred to `address(0)` — permanently burned.
4. Pool calls `metricOmmSwapCallback`; `_justPayCallback` pulls 1000 USDC from the user and sends it to the pool.
5. `amountOut` is checked against `params.amountOutMinimum`. If `amountOutMinimum == 0` (common in integrations that set slippage separately), the check passes.
6. Transaction succeeds. User has paid 1000 USDC and received 0 ETH. Funds are unrecoverable. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L235-245)
```text
  function _validatePath(address[] calldata tokens, address[] calldata pools, bytes[] calldata extensionDatas)
    internal
    pure
  {
    if (
      tokens.length < 2 || pools.length != tokens.length - 1 || extensionDatas.length != pools.length
        || pools.length > MAX_PATH_POOLS
    ) {
      revert InvalidPath();
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
