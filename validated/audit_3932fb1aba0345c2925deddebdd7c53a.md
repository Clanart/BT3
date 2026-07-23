The code is confirmed. Let me verify the pool's balance check mechanism to ensure the attack path is complete.

Audit Report

## Title
Missing intermediate-token connectivity validation in `exactInput` allows draining router's stranded ERC20 balances — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

## Summary
`exactInput` sets the per-hop callback payment token to the caller-supplied `params.tokens[i]` without verifying it equals the actual output token of `params.pools[i-1]`. An attacker can supply a mismatched `tokens[1]` pointing to any ERC20 the router holds, causing the router to drain that token to an attacker-chosen pool while the legitimate intermediate token from hop 0 is stranded. `MetricOmmSwapPath.poolsAreConnected` exists and implements exactly this check but is never called from `exactInput`.

## Finding Description
In `exactInput` the loop sets the transient callback context for each hop:

```solidity
_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY,
    i == 0 ? msg.sender : address(this), params.tokens[i]);
``` [1](#0-0) 

For `i ≥ 1`, `params.tokens[i]` is fully attacker-controlled. When pool `i` calls `metricOmmSwapCallback`, `_justPayCallback` executes:

```solidity
pay(_getTokenToPay(), _getPayer(), msg.sender, uint256(...));
// → IERC20(params.tokens[i]).safeTransfer(pool_i, amount)
``` [2](#0-1) 

With `payer == address(this)`, `pay` unconditionally calls `IERC20(token).safeTransfer(recipient, value)`: [3](#0-2) 

The pool's post-callback balance check only verifies that the pool received enough of its own tokens — it does not care which address sent them or what the router believes the token is: [4](#0-3) 

`_validatePath` only checks array lengths: [5](#0-4) 

`MetricOmmSwapPath.poolsAreConnected` implements the missing check — it queries each pool's actual token pair and asserts the output token of pool `i` equals the input token of pool `i+1` — but it is never called from `exactInput`: [6](#0-5) 

**Exploit flow:**
1. Router holds `N` of `tokenX` (stranded from a prior failed multi-hop swap).
2. Attacker calls `exactInput` with `tokens=[tokenA, tokenX, tokenC]`, `pools=[pool0 (tokenA/tokenB), pool1 (tokenX/tokenC)]`.
3. Hop 0: router pays `tokenA` from `msg.sender` → `pool0`; router receives `tokenB` (amount `M`).
4. Hop 1: callback context is set with `tokenX` as token-to-pay, `address(this)` as payer. `pool1.swap(recipient, ..., M, ...)` is called. Pool1 calls back; router executes `IERC20(tokenX).safeTransfer(pool1, M)`. Pool1's `IncorrectDelta` check passes because it received its own input token. Pool1 sends `tokenC` to attacker's `recipient`.
5. Router's `tokenX` balance is drained by `M`; `tokenB` is stranded in the router.

## Impact Explanation
Any ERC20 token balance held by the router can be drained by any unprivileged caller via a crafted `exactInput` path. The `sweepToken` function's existence confirms the router is designed to hold residual balances: [7](#0-6) 

Tokens stranded in the router from failed multi-hop swaps belong to users who intended to recover them. Draining them constitutes direct loss of user principal. Severity is High: loss is bounded by the router's current balance of the targeted token, which is a realistic non-zero value on any active deployment.

## Likelihood Explanation
The router transiently holds intermediate tokens during every multi-hop swap. Any transaction that reverts after hop 0 completes (e.g., slippage revert, gas exhaustion, or extension hook revert) leaves the hop-0 output stranded. The attack requires only: (a) the router holds some balance of token X, and (b) a factory pool exists with token X as an input token — both are routine conditions. The attack is permissionless, repeatable, and requires no privileged access.

## Recommendation
Call `MetricOmmSwapPath.poolsAreConnected` (which already exists) inside `exactInput` before the hop loop, or inline the equivalent check: after each hop `i ≥ 1`, assert `params.tokens[i] == MetricOmmSwapPath.hopOutputToken(params.pools[i-1], zeroForOne_i_minus_1)`. Alternatively, derive the full token sequence from pool immutables rather than accepting `tokens[]` as user input.

## Proof of Concept
```solidity
// Setup: router holds 1000e18 tokenX (simulate stranded balance)
tokenX.transfer(address(router), 1000e18);

// pool0: tokenA/tokenB; pool1: tokenX/tokenC (attacker-chosen)
ExactInputParams memory params = ExactInputParams({
    tokens: [tokenA, tokenX, tokenC],   // tokens[1]=tokenX is WRONG (should be tokenB)
    pools: [pool0, pool1],
    zeroForOneBitMap: ...,
    amountIn: 100e18,                   // tokenA input
    amountOutMinimum: 1,
    deadline: block.timestamp,
    extensionDatas: ["", ""],
    recipient: attacker
});

router.exactInput(params);

// Result: router's tokenX drained, tokenB stranded
assertEq(tokenX.balanceOf(address(router)), 0);
assertGt(tokenB.balanceOf(address(router)), 0); // stranded
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-103)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L48-55)
```text
  function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);

    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L71-72)
```text
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L257-263)
```text
      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
```

**File:** metric-periphery/contracts/libraries/MetricOmmSwapPath.sol (L43-53)
```text
  function poolsAreConnected(address[] calldata pools, uint256 zeroForOneBitMap) internal view returns (bool) {
    uint256 last = pools.length - 1;
    for (uint256 i = 0; i < last; i++) {
      bool zeroForOne = resolveZeroForOneBitmap(zeroForOneBitMap, i);
      bool nextZeroForOne = resolveZeroForOneBitmap(zeroForOneBitMap, i + 1);
      if (hopOutputToken(pools[i], zeroForOne) != hopInputToken(pools[i + 1], nextZeroForOne)) {
        return false;
      }
    }
    return true;
  }
```
