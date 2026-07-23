### Title
Missing Factory Validation in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain Approved User Funds — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address from the caller and stores it as the trusted callback counterparty in transient storage. Because the contract never validates the supplied address against the `MetricOmmPoolFactory`, an attacker can deploy a malicious pool contract, trick a victim into calling `addLiquidityExactShares` or `addLiquidityWeighted` with that address, and have the malicious pool drain the victim's pre-approved tokens up to the caller-supplied `maxAmountToken0` / `maxAmountToken1` caps.

---

### Finding Description

The NatSpec on the contract explicitly acknowledges the gap:

> *"The caller is responsible for supplying a legitimate pool address and other non-malicious parameters. This contract does not verify the pool against the factory; a malicious pool can request token pulls up to the caller-provided max caps during callback settlement."* [1](#0-0) 

Despite this warning, no on-chain guard enforces it. The internal `_addLiquidity` flow is:

1. `_setPayContext(pool, payer, maxAmountToken0, maxAmountToken1)` — stores the **caller-supplied** `pool` as the authorised callback caller. [2](#0-1) 

2. `IMetricOmmPoolActions(pool).addLiquidity(...)` — calls into the **unvalidated** pool. [3](#0-2) 

3. Inside `metricOmmModifyLiquidityCallback`, the only caller check is `msg.sender != expectedPool` — but `expectedPool` **is** the malicious pool, so the check passes trivially. [4](#0-3) 

4. The callback then calls `IMetricOmmPool(msg.sender).getImmutables()` to learn `token0`/`token1` — values the malicious pool controls — and immediately pulls those tokens from the victim. [5](#0-4) 

The analog to the Rocketpool bug is exact: validation is delegated to the caller (documentation / "CLI level"), while the contract itself performs no on-chain check — precisely the pattern the external report flags as insufficient.

---

### Impact Explanation

A victim who has approved `MetricOmmPoolLiquidityAdder` to spend their ERC-20 tokens loses up to `maxAmountToken0` units of any token the malicious pool declares as `token0`, and up to `maxAmountToken1` units of any token declared as `token1`. Both amounts are bounded only by the victim's own slippage caps, which are user-supplied and can be set to the victim's full approval allowance. This is a **direct loss of user principal**.

---

### Likelihood Explanation

The attack requires:
- Deploying a malicious pool contract (permissionless, trivial).
- Inducing the victim to call `addLiquidityExactShares` or `addLiquidityWeighted` with the malicious pool address — achievable via a phishing UI, a compromised front-end, or a misleading pool listing that passes no on-chain legitimacy check.

Users who have granted large standing approvals to the adder (a common pattern for convenience) are at highest risk.

---

### Recommendation

Add a factory-membership check before storing the pool in transient context:

```solidity
// In _addLiquidity, before _setPayContext:
require(
    IMetricOmmPoolFactory(factory).isPool(pool),
    "UnknownPool"
);
```

This mirrors the fix the Rocketpool report recommends: move validation from the caller/documentation layer into the lowest-level contract function so every integration path is protected automatically.

---

### Proof of Concept

```solidity
// Attacker deploys:
contract MaliciousPool {
    address victim;
    address adder;
    address stolenToken;

    function addLiquidity(address, uint80, LiquidityDelta calldata, bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // callback into adder — msg.sender == this == expectedPool ✓
        IMetricOmmModifyLiquidityCallback(adder)
            .metricOmmModifyLiquidityCallback(
                MAX_AMOUNT_0,   // drain full cap
                MAX_AMOUNT_1,
                abi.encode(uint8(1)) // KIND_PAY
            );
        return (MAX_AMOUNT_0, MAX_AMOUNT_1);
    }

    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = stolenToken; // any token victim approved
        imm.token1 = anotherStolenToken;
    }
}

// Victim (tricked by phishing UI) calls:
adder.addLiquidityExactShares(
    address(maliciousPool),
    victim,
    0,
    deltas,
    1_000_000e6,  // maxAmountToken0 — full USDC allowance
    500e18,       // maxAmountToken1 — full WETH allowance
    ""
);
// Result: malicious pool receives 1 000 000 USDC + 500 WETH from victim.
```

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-164)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L193-193)
```text
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-196)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```
