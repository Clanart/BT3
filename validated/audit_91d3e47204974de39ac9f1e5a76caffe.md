### Title
Caller-Supplied Zero Class Hash in Simulated `DeclareV1` Produces Wrong `deprecated_declared_classes` in `induced_state_diff` — (`File: crates/apollo_rpc_execution/src/lib.rs`)

---

### Summary

When `starknet_simulateTransactions` processes a `BroadcastedDeclareTransaction::V1`, the `class_hash` field is unconditionally set to `ClassHash::default()` (zero) as a struct-filling placeholder. That zero value is then extracted verbatim as `deprecated_declared_class_hash` and injected into `induced_state_diff`, causing the simulation to return `deprecated_declared_classes: [0x0]` for every `DeclareV1` simulation regardless of the actual class being declared.

---

### Finding Description

**Step 1 — Zero class hash injected at the RPC boundary.**

In `crates/apollo_rpc/src/v0_8/api/mod.rs`, the `TryFrom<BroadcastedDeclareTransaction> for ExecutableTransactionInput` conversion for `V1` explicitly sets `class_hash: ClassHash::default()`:

```rust
Ok(Self::DeclareV1(
    starknet_api::transaction::DeclareTransactionV0V1 {
        max_fee,
        signature,
        nonce,
        // The blockifier doesn't need the class hash, but it uses the SN_API
        // DeclareTransactionV0V1 which requires it.
        class_hash: ClassHash::default(),   // ← always zero
        sender_address,
    },
    sn_api_contract_class,
    abi_length,
    false,
))
``` [1](#0-0) 

**Step 2 — Zero class hash extracted as `deprecated_declared_class_hash`.**

`execute_transactions` in `crates/apollo_rpc_execution/src/lib.rs` determines the deprecated class hash by pattern-matching on the transaction variant and reading the `class_hash` field directly:

```rust
let deprecated_declared_class_hash = match &tx {
    ExecutableTransactionInput::DeclareV0(
        DeclareTransactionV0V1 { class_hash, .. }, _, _, _,
    ) => Some(*class_hash),
    ExecutableTransactionInput::DeclareV1(
        DeclareTransactionV0V1 { class_hash, .. }, _, _, _,
    ) => Some(*class_hash),   // ← picks up ClassHash(0)
    _ => None,
};
``` [2](#0-1) 

**Step 3 — Zero class hash written into the state diff.**

`induced_state_diff` in `crates/apollo_rpc_execution/src/execution_utils.rs` blindly places the caller-supplied value into `deprecated_declared_classes` without cross-checking it against the actual blockifier execution state:

```rust
Ok(ThinStateDiff {
    ...
    deprecated_declared_classes: deprecated_declared_class_hash
        .map_or_else(Vec::new, |class_hash| vec![class_hash]),
    ...
})
``` [3](#0-2) 

The `CommitmentStateDiff` produced by the blockifier has no `deprecated_declared_classes` field at all — it only tracks `class_hash_to_compiled_class_hash` for Sierra classes. For Cairo 0 classes the blockifier does not record the declaration in the state maps, so the caller must supply the hash. The code trusts the caller-supplied value without verifying it against the contract class that was actually executed. [4](#0-3) 

**Analog to the YoloV2 bug.**

| YoloV2 | Sequencer |
|---|---|
| Token *address* is whitelisted; token *type* (ERC20 vs ERC721) is caller-supplied and unchecked | Class *body* is executed; `deprecated_declared_class_hash` is caller-supplied (zero placeholder) and unchecked against the executed class |
| Caller fills `tokenIdsOrAmounts` with zeros → `transferFrom(from, to, 0)` succeeds → free entries | Caller's `class_hash` field is zero → `deprecated_declared_classes: [0x0]` in simulation output → wrong authoritative state diff |

---

### Impact Explanation

`starknet_simulateTransactions` returns an authoritative-looking wrong value: `deprecated_declared_classes` always contains `[0x0]` for every simulated `DeclareV1` transaction, regardless of the actual class hash being declared. Any client or tooling that reads the simulation's `induced_state_diff` to understand what deprecated classes a transaction will declare receives a systematically incorrect answer. This matches the allowed impact: **High — RPC simulation returns an authoritative-looking wrong value.** [5](#0-4) 

---

### Likelihood Explanation

The path is unconditional and deterministic: every call to `starknet_simulateTransactions` with a `BroadcastedDeclareTransaction::V1` triggers it. No special privileges or unusual conditions are required. The `TryFrom` conversion is the only code path for broadcast-simulate `DeclareV1` transactions.

---

### Recommendation

Compute the actual class hash from the contract class body inside the `BroadcastedDeclareTransaction::V1` arm before constructing `ExecutableTransactionInput::DeclareV1`, rather than using `ClassHash::default()`. The hash should be derived from the `DeprecatedContractClass` that is already available at that point (`sn_api_contract_class`), so that `execute_transactions` extracts the correct value and `induced_state_diff` populates `deprecated_declared_classes` with the real class hash.

---

### Proof of Concept

1. Call `starknet_simulateTransactions` with a `BroadcastedDeclareTransaction::V1` carrying any valid Cairo 0 contract class.
2. Inspect the returned `induced_state_diff.deprecated_declared_classes`.
3. Observe it always equals `[0x0]` regardless of the actual class hash of the submitted contract.

The existing (currently `#[ignore]`d) test `induced_state_diff` in `crates/apollo_rpc_execution/src/execution_test.rs` demonstrates the expected correct value for a `declare_deprecated_class` scenario:

```rust
let expected_declare_deprecated_class = ThinStateDiff {
    deprecated_declared_classes: vec![class_hash!(next_declared_class_hash)],

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L494-508)
```rust
                Ok(Self::DeclareV1(
                    starknet_api::transaction::DeclareTransactionV0V1 {
                        max_fee,
                        signature,
                        nonce,
                        // The blockifier doesn't need the class hash, but it uses the SN_API
                        // DeclareTransactionV0V1 which requires it.
                        class_hash: ClassHash::default(),
                        sender_address,
                    },
                    sn_api_contract_class,
                    abi_length,
                    // TODO(yair): pass the right value for only_query field.
                    false,
                ))
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L667-670)
```rust
struct TransactionExecutionOutput {
    execution_info: TransactionExecutionInfo,
    induced_state_diff: ThinStateDiff,
    price_unit: PriceUnit,
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L733-747)
```rust
        let deprecated_declared_class_hash = match &tx {
            ExecutableTransactionInput::DeclareV0(
                DeclareTransactionV0V1 { class_hash, .. },
                _,
                _,
                _,
            ) => Some(*class_hash),
            ExecutableTransactionInput::DeclareV1(
                DeclareTransactionV0V1 { class_hash, .. },
                _,
                _,
                _,
            ) => Some(*class_hash),
            _ => None,
        };
```

**File:** crates/apollo_rpc_execution/src/execution_utils.rs (L137-144)
```rust
    Ok(ThinStateDiff {
        deployed_contracts: blockifier_state_diff.address_to_class_hash,
        storage_diffs: blockifier_state_diff.storage_updates,
        class_hash_to_compiled_class_hash: blockifier_state_diff.class_hash_to_compiled_class_hash,
        deprecated_declared_classes: deprecated_declared_class_hash
            .map_or_else(Vec::new, |class_hash| vec![class_hash]),
        nonces: blockifier_state_diff.address_to_nonce,
    })
```

**File:** crates/blockifier/src/state/cached_state.rs (L700-710)
```rust
#[cfg_attr(feature = "transaction_serde", derive(serde::Serialize, serde::Deserialize))]
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct CommitmentStateDiff {
    // Contract instance attributes (per address).
    pub address_to_class_hash: IndexMap<ContractAddress, ClassHash>,
    pub address_to_nonce: IndexMap<ContractAddress, Nonce>,
    pub storage_updates: IndexMap<ContractAddress, IndexMap<StorageKey, Felt>>,

    // Global attributes.
    pub class_hash_to_compiled_class_hash: IndexMap<ClassHash, CompiledClassHash>,
}
```
