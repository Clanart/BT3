The key question is whether OpenZeppelin's `Multicall` uses `delegatecall` (which preserves `msg.value` across all sub-calls). Let me confirm the OZ implementation and the `register` function's payable check.

The vulnerability is real. Here is the analysis:

**Root cause:** `OracleBase` inherits OpenZeppelin's `Multicall`, which executes each sub-call via `delegatecall`. `delegatecall` preserves `msg.value` from the outer transaction across every iteration of the loop. The `register` function is `payable` and checks `msg.value >= registrationFee` — but that check reads the *original* `msg.value` in every sub-call, not a per-call portion of it.

---

### Title
`msg.value` Reuse via `delegatecall` in `OracleBase.multicall` Allows Bypassing Per-Registration Fee — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

### Summary
`OracleBase` inherits OpenZeppelin's `Multicall`, which batches calls using `delegatecall`. Because `delegatecall` preserves `msg.value` across all sub-calls, an attacker can batch N `register()` calls with only one fee payment and successfully register N pools.

### Finding Description
`OracleBase` is declared as:

```solidity
contract OracleBase is AccessControl, Multicall, IOffchainOracle {
``` [1](#0-0) 

The `register` function enforces a fee per registration:

```solidity
function register(bytes32 feedId, address pool, address factory) external payable {
    require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
``` [2](#0-1) 

OpenZeppelin's `Multicall.multicall` is `payable` and dispatches each element via `delegatecall`. `delegatecall` does not split or consume `msg.value` — it forwards the full original `msg.value` to every sub-call. The `IMulticall` interface in this repo explicitly documents this:

```solidity
/// @notice Executes each calldata element on this contract via delegatecall.
function multicall(bytes[] calldata data) external payable returns (bytes[] memory results);
``` [3](#0-2) 

So when an attacker calls `multicall([register(feedId1,pool1,factory), register(feedId2,pool2,factory)])` with `msg.value == registrationFee`, both sub-calls see `msg.value == registrationFee`, both pass the `require`, and both pools are registered. Only one fee is collected.

### Impact Explanation
- The oracle admin loses `(N-1) * registrationFee` in protocol fee revenue for every N-registration batch.
- The fee-as-spam-deterrent is fully bypassed: an attacker can register an unbounded number of pools for the cost of a single fee, flooding the oracle's `registeredPool` mapping and enabling oracle reads for pools that did not pay.
- The `registrationFee` is tunable by ADMIN and is explicitly described as a spam deterrent; bypassing it is a direct protocol fee loss and a broken access-control invariant.

### Likelihood Explanation
Any unprivileged caller who can supply two valid `(feedId, pool, factory)` tuples — where `factory` is approved and `pool` is recognized by that factory — can exploit this. No special role is required. The only precondition is having legitimate pools, which is the normal state of the system.

### Recommendation
Remove `payable` from `register` and collect fees via an explicit `transferFrom` (ERC-20), or track cumulative fees consumed within the `multicall` context using a transient storage counter that is decremented per sub-call. Alternatively, do not inherit `Multicall` on contracts with `payable` functions that check `msg.value`.

### Proof of Concept
```solidity
// Foundry test sketch
function test_multicall_fee_bypass() public {
    uint256 fee = oracle.registrationFee(); // e.g. 1 ether

    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeCall(oracle.register, (feedId1, pool1, factory));
    calls[1] = abi.encodeCall(oracle.register, (feedId2, pool2, factory));

    oracle.multicall{value: fee}(calls); // only one fee paid

    assertTrue(oracle.registeredPool(feedId1, pool1)); // registered
    assertTrue(oracle.registeredPool(feedId2, pool2)); // also registered
    assertEq(address(oracle).balance, fee);            // only one fee collected
}
```

The default `registrationFee` is `1 wei` [4](#0-3) 
but ADMIN is expected to raise it as a spam deterrent [5](#0-4) 
— the bypass is effective at any non-zero fee value.

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L18-18)
```text
contract OracleBase is AccessControl, Multicall, IOffchainOracle {
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L53-53)
```text
        registrationFee = 1 wei; // very cheap default; ADMIN tunes via setRegistrationFee
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L201-202)
```text
    function register(bytes32 feedId, address pool, address factory) external payable {
        require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L254-257)
```text
    function setRegistrationFee(uint256 newFee) external onlyRole(ADMIN_ROLE) {
        uint256 oldFee = registrationFee;
        registrationFee = newFee;
        emit RegistrationFeeUpdated(oldFee, newFee);
```

**File:** metric-periphery/contracts/interfaces/IMulticall.sol (L7-10)
```text
  /// @notice Executes each calldata element on this contract via delegatecall.
  /// @param data Encoded function calls to batch.
  /// @return results Return data for each batched call, in order.
  function multicall(bytes[] calldata data) external payable returns (bytes[] memory results);
```
