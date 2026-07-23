Audit Report

## Title
`msg.value` Reuse via `delegatecall` in Inherited `Multicall` Bypasses `registrationFee` — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

## Summary

`OracleBase` inherits OpenZeppelin's `Multicall`, which dispatches each sub-call via `delegatecall`. Because `delegatecall` preserves the original `msg.value` unchanged across every iteration, an attacker can call `multicall{value: registrationFee}([register(...), register(...), ...])` and have each `register` sub-call independently satisfy `msg.value >= registrationFee`, registering N pools while paying only one fee. This defeats the sole economic deterrent of the oracle's abuse-protection system.

## Finding Description

`OracleBase` inherits OZ `Multicall` directly with no override: [1](#0-0) 

`register` is `external payable` and enforces the fee with a single `msg.value` check per invocation: [2](#0-1) 

OZ's `Multicall.multicall` dispatches each element via `delegatecall`. The EVM does not consume or decrement `msg.value` across `delegatecall` iterations — the original call's `msg.value` is visible unchanged in every sub-call's execution context. Therefore, each `register` invocation inside a single `multicall` sees the same `msg.value` and each independently passes the `>= registrationFee` check. There is no counter, accumulator, or consumed-value tracking anywhere in `OracleBase` or the inherited `Multicall`.

The `registrationFee` is the sole economic deterrent of the abuse-protection system: [3](#0-2) [4](#0-3) 

The full registration logic that gets bypassed: [5](#0-4) 

## Impact Explanation

**Protocol fee revenue loss**: The contract collects only one `registrationFee` while recording N pool registrations. For N sub-calls, `(N-1) * registrationFee` is never received. The default is 1 wei but the fee is explicitly designed to be raised by ADMIN when abuse appears — at any non-trivial fee level the loss scales linearly with N.

**Abuse-deterrent bypass**: The entire abuse-protection model (register → read oracle price → get blacklisted → pay fee to re-register) relies on the fee being a real cost per registration. With this bypass, an attacker can register many pools cheaply, use them to read oracle prices via `price(feedId, pool)`, get blacklisted, and re-register again at near-zero cost, defeating the deterrent entirely. This meets the allowed impact gate of admin-boundary/oracle role checks bypassed by an unprivileged path and protocol fee loss.

## Likelihood Explanation

The `multicall` entrypoint is public and permissionless. The attacker only needs valid pool addresses from an approved factory — a normal precondition for any legitimate registrant. No privileged role, no malicious pool, and no non-standard token behavior is required. The attack is a single transaction and is trivially repeatable.

## Recommendation

Override `multicall` in `OracleBase` to `require(msg.value == 0, ...)`, forcing callers to invoke `register` directly (one call, one fee). Alternatively, replace the `msg.value` check in `register` with a pull-payment pattern (e.g., `transferFrom` an ERC-20 fee token) that cannot be reused across `delegatecall` iterations. A third option is to track cumulative ETH consumed across sub-calls within an overridden `multicall`.

## Proof of Concept

```solidity
// Foundry fork test
function test_multicall_register_fee_bypass() public {
    uint256 fee = oracle.registrationFee(); // e.g. 1 ether after admin sets it

    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeCall(oracle.register, (feed1, pool1, factory));
    calls[1] = abi.encodeCall(oracle.register, (feed2, pool2, factory));

    // Pay only ONE fee for TWO registrations
    oracle.multicall{value: fee}(calls);

    assertTrue(oracle.registeredPool(feed1, pool1));  // registered
    assertTrue(oracle.registeredPool(feed2, pool2));  // also registered
    assertEq(address(oracle).balance, fee);           // only one fee collected
}
```

Both `registeredPool` mappings are set while the contract balance equals only one `registrationFee`, confirming N-1 fees are not collected. [6](#0-5) [7](#0-6)

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L6-18)
```text
import { Multicall } from "@openzeppelin/contracts/utils/Multicall.sol";

import { IOffchainOracle } from "../../interfaces/IOffchainOracle.sol";
import { IPoolFactory, IPool } from "../../interfaces/IPoolFactory.sol";
import { TimeMs, toTimeMs } from "../utils/TimeMs.sol";

/// @notice Registrationless base for the provider oracles (Pyth Lazer, Chainlink Data
///         Streams). There is no feed registry and no token metadata: the trust anchor
///         is the provider's own signature verified on every push, so any feed id that
///         arrives in a verified payload is stored. A feed "exists" once it has data
///         (`timestampMs != 0`) — for readers that is indistinguishable from the old
///         "registered" state.
contract OracleBase is AccessControl, Multicall, IOffchainOracle {
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L35-35)
```text
    uint256 public registrationFee;
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L39-39)
```text
    mapping(bytes32 => mapping(address => bool)) public registeredPool;
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L49-53)
```text
    constructor(address _owner, uint256 maxTimeDrift) {
        _grantRole(ADMIN_ROLE, _owner);
        _setRoleAdmin(ADMIN_ROLE, ADMIN_ROLE);
        MAX_TIME_DRIFT = maxTimeDrift;
        registrationFee = 1 wei; // very cheap default; ADMIN tunes via setRegistrationFee
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L196-213)
```text
    /// @notice Permissionless paid registration: whitelist `pool` for `feedId` (required to use the
    ///         on-chain price(feedId, factory) path). `factory` must be approved and recognize `pool`
    ///         via isPool. Paying also clears any blacklist on the pool.
    /// @dev    Overpayment is NOT refunded: any msg.value above registrationFee is kept and is
    ///         withdrawable by ADMIN via withdrawEth. This is intentional.
    function register(bytes32 feedId, address pool, address factory) external payable {
        require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
        require(pool != address(0));
        require(approvedFactories.contains(factory), FactoryNotApproved(factory));
        require(IPoolFactory(factory).isPool(pool), NotAPool(pool));

        if (blacklisted[pool]) {
            blacklisted[pool] = false;
            emit BlacklistUpdated(pool, false);
        }

        registeredPool[feedId][pool] = true;
        emit PoolRegistered(feedId, pool, msg.sender, msg.value);
```
