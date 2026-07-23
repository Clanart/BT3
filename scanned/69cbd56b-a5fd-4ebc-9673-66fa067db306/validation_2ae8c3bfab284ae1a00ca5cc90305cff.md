### Title
Missing Zero Address Validation for Immutable `weth` and `factory` Constructor Parameters — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`, `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` store `weth` (and `factory` for the router) as immutable values set only in their constructors, with no zero-address guards. A deployment-time misconfiguration permanently breaks all ETH-denominated swap and liquidity flows, and — in the router case — may corrupt the callback-caller validation that protects user funds from being drained by an unauthorized caller.

---

### Finding Description

**`MetricOmmSimpleRouter` constructor** accepts `weth` and `factory` without any non-zero check:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 24
constructor(address weth, address factory)
    MetricOmmSwapRouterBase(factory)
    PeripheryPayments(weth)
{}
```

**`MetricOmmPoolLiquidityAdder` constructor** accepts `weth` without any non-zero check:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol  line 37
constructor(address weth) PeripheryPayments(weth) {}
```

Both values are forwarded to base contracts and stored as immutables. They cannot be changed after deployment.

**Scenario A — `weth = address(0)`:**  
`PeripheryPayments.pay()` uses the stored `weth` address to decide whether to wrap/unwrap native ETH. With `weth == address(0)`, the WETH-branch condition (`token == WETH`) is never satisfied for any real token, so:
- Users who send ETH with `exactInputSingle` / `exactInput` / `addLiquidityExactShares` expecting it to be wrapped have their ETH accepted by the `payable` entry points but the subsequent `pay()` call cannot route it correctly. The ETH is permanently trapped in the router/adder.
- All ETH-denominated swap and liquidity flows are permanently unusable.

**Scenario B — `factory = address(0)` (router only):**  
`MetricOmmSwapRouterBase` uses `factory` inside `_requireExpectedCallbackCaller` to verify that the `msg.sender` of `metricOmmSwapCallback` is a pool deployed by the legitimate factory. With `factory == address(0)`, this factory-membership check is broken. An attacker who can call `metricOmmSwapCallback` while a legitimate swap is in flight (transient payer context is set) can supply crafted `amount0Delta`/`amount1Delta` values and cause `pay()` to pull tokens from the stored payer (the victim user who approved the router).

---

### Impact Explanation

- **Scenario A:** ETH sent by users to the router or liquidity adder is permanently locked. All native-ETH swap and liquidity flows are irreversibly broken. Matches: *loss of user principal* and *unusable swap/liquidity flows*.
- **Scenario B:** Callback-caller guard is bypassed; an attacker can drain ERC-20 tokens from any user who has approved the router and has an in-flight swap. Matches: *direct loss of user principal*.

---

### Likelihood Explanation

Likelihood is low but non-negligible: the constructor parameters are plain `address` arguments with no type-level enforcement, and deployment scripts or factory wrappers that pass a wrong value (e.g., a zero placeholder, a mis-ordered argument) would silently succeed. Because the variables are immutable, there is no recovery path — the contract must be redeployed. The Portal audit accepted the identical bug class at **Medium** severity for the same reason.

---

### Recommendation

Add explicit zero-address guards at the top of each constructor:

```solidity
// MetricOmmSimpleRouter
constructor(address weth, address factory)
    MetricOmmSwapRouterBase(factory)
    PeripheryPayments(weth)
{
    if (weth == address(0)) revert ZeroAddress();
    if (factory == address(0)) revert ZeroAddress();
}

// MetricOmmPoolLiquidityAdder
constructor(address weth) PeripheryPayments(weth) {
    if (weth == address(0)) revert ZeroAddress();
}
```

---

### Proof of Concept

**Scenario A — ETH permanently trapped:**

1. Deploy `MetricOmmSimpleRouter(address(0), validFactory)`.
2. User calls `exactInputSingle{value: 1 ether}(params)` where `params.tokenIn` is the WETH address.
3. Inside the swap callback, `_justPayCallback` calls `pay(weth_stored, payer, pool, amount)`.
4. `pay()` checks `token == WETH` → `realWethAddress == address(0)` → false; falls through to `IERC20(address(0)).transferFrom(...)` which reverts.
5. The entire swap reverts, but if the router has a `receive()` fallback (common for WETH-unwrap support), ETH sent in a prior multicall leg is already held by the contract and cannot be recovered.

**Scenario B — callback guard broken (router):**

1. Deploy `MetricOmmSimpleRouter(validWeth, address(0))`.
2. Alice calls `exactInputSingle` → router sets transient payer = Alice, expected pool = legitimatePool.
3. Before the pool's callback fires, attacker (in the same block, different tx or via reentrancy if guard is absent) calls `metricOmmSwapCallback(largePositiveDelta, 0, "")`.
4. `_requireExpectedCallbackCaller(msg.sender)` uses the broken factory (`address(0)`) to validate — check passes or reverts incorrectly.
5. `_justPayCallback` calls `pay(token, Alice, attacker, largeAmount)` → Alice's approved tokens are transferred to the attacker. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L24-24)
```text
  constructor(address weth, address factory) MetricOmmSwapRouterBase(factory) PeripheryPayments(weth) {}
```

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L37-37)
```text
  constructor(address weth) PeripheryPayments(weth) {}
```
