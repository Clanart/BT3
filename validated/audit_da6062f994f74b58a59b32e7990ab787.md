Now I have enough information to make a definitive assessment. Let me check one more critical detail about the `get_compiled_class` path for V0 classes.

### Title
Missing Content-Hash Verification for Deprecated Classes in P2P Sync — (`crates/apollo_p2p_sync/src/client/class.rs`)

---

### Summary

A malicious p2p peer can supply a `DeprecatedContractClass` whose Pedersen content hash does not equal the `class_hash` key it is paired with. The p2p sync client accepts and stores the mismatched class without error. Any subsequent execution that looks up that class hash retrieves and runs the wrong bytecode, with no downstream hash check to catch the substitution.

---

### Finding Description

**Entrypoint — `parse_data_for_block`**

When the class sync stream receives `(ApiContractClass::DeprecatedContractClass(deprecated_contract_class), class_hash)` from a peer, the only validation performed is:

```rust
deprecated_declared_classes.contains(&class_hash)
``` [1](#0-0) 

This confirms that `class_hash` appears in the already-committed state diff, but it does **not** verify that `compute_deprecated_class_hash(&deprecated_contract_class) == class_hash`. The class content is accepted verbatim.

**Storage — `write_to_storage` → `add_deprecated_class`**

The accepted `(class_hash, deprecated_class)` pair is forwarded directly to the class manager:

```rust
class_manager_client
    .add_deprecated_class(class_hash, deprecated_class.clone())
    .await
``` [2](#0-1) 

`ClassManager::add_deprecated_class` calls `set_deprecated_class(class_id, class)` with no hash computation or comparison: [3](#0-2) 

`FsClassStorage::set_deprecated_class` has an idempotency guard (skips if the file already exists), but on a fresh sync the malicious class is written atomically to disk under the path derived from `class_id = H`. [4](#0-3) 

**Execution — `get_compiled_class` (V0 path)**

When a contract deployed to class hash `H` is executed, `get_compiled_class(H)` retrieves whatever is stored under `H` and returns it directly for execution. For V0 (deprecated) classes there is an explicit, acknowledged absence of hash verification:

```rust
// TODO(shahak): Verify cairo0 as well after get_class_definition_block_number is fixed.
ContractClass::V0(deprecated_contract_class) => {
    Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
}
``` [5](#0-4) 

The TODO confirms the team is aware the V0 path skips verification, but it is not yet fixed.

**Contrast with Sierra (V1) path**

For Cairo 1 classes, `add_class` computes the class hash internally and returns it; the central sync path even panics on mismatch:

```rust
if class_hash != *expected_class_hash {
    panic!("Class hash mismatch. Expected: {expected_class_hash}, got: {class_hash}.");
}
``` [6](#0-5) 

No equivalent guard exists anywhere in the deprecated-class path.

---

### Impact Explanation

A syncing node that stores `evil_class` under `H` will execute `evil_class` for every contract whose `class_hash` is `H`. This affects:

- **RPC execution / fee estimation / tracing / simulation** — all return results computed from the wrong bytecode, with no indication of corruption.
- **Wrong contract code selected for execution** — the substituted class can have arbitrary entry points, bytecode, and storage effects.

The SNOS/prover Cairo code does recompute `deprecated_compiled_class_hash` and would reject a mismatched class during proof generation, but syncing follower nodes serving RPC calls do not go through that path. [7](#0-6) 

---

### Likelihood Explanation

Any node reachable on the p2p network can act as a peer. The attacker needs only to:
1. Know a `class_hash` `H` that appears in `deprecated_declared_classes` of a block the victim has not yet synced.
2. Send `(evil_class, H)` as the class response for that block.

No operator privileges are required. The idempotency guard in `set_deprecated_class` means the attack window is the first time the victim syncs a block containing `H`; after that the slot is occupied.

---

### Recommendation

In `parse_data_for_block`, after receiving a `DeprecatedContractClass`, compute its Pedersen hash and reject the peer if it does not match the accompanying `class_hash`:

```rust
ApiContractClass::DeprecatedContractClass(ref deprecated_contract_class) => {
    let computed = compute_deprecated_class_hash(deprecated_contract_class)
        .map_err(|_| ParseDataError::BadPeer(BadPeerError::InvalidDeprecatedClassHash { class_hash }))?;
    if ClassHash(computed) != class_hash {
        return Err(ParseDataError::BadPeer(BadPeerError::InvalidDeprecatedClassHash { class_hash }));
    }
    // ... existing insert logic
}
```

Additionally, remove the TODO in `get_compiled_class` and add the V0 hash check once `get_class_definition_block_number` is fixed. [8](#0-7) 

---

### Proof of Concept

```rust
// Pseudocode unit test (no operator privileges needed)
let legitimate_class_hash = H; // from state_diff.deprecated_declared_classes
let evil_class = DeprecatedContractClass { /* different bytecode */ };

// Precondition: compute_deprecated_class_hash(&evil_class) != H
assert_ne!(ClassHash(compute_deprecated_class_hash(&evil_class).unwrap()), H);

// Simulate peer sending (evil_class, H)
// parse_data_for_block only checks: deprecated_declared_classes.contains(&H) → true
// write_to_storage calls: add_deprecated_class(H, evil_class) → Ok(())
// ClassManager stores evil_class under H without error

// Retrieval
let stored = class_manager.get_executable(H).unwrap().unwrap();
// stored == evil_class, not the class whose hash is H
// get_compiled_class(H) returns RunnableCompiledClass::V0(evil_class) for execution
```

The existing test `class_manager_get_executable` already demonstrates that `add_deprecated_class` accepts an arbitrary `ClassHash(felt!("0x1806"))` paired with `DeprecatedContractClass::default()` without any hash check, confirming the storage layer imposes no constraint. [9](#0-8)

### Citations

**File:** crates/apollo_p2p_sync/src/client/class.rs (L54-55)
```rust
                while let Err(err) = class_manager_client
                    .add_deprecated_class(class_hash, deprecated_class.clone())
```

**File:** crates/apollo_p2p_sync/src/client/class.rs (L131-142)
```rust
                let (is_declared, duplicate_class) = match api_contract_class {
                    ApiContractClass::ContractClass(contract_class) => (
                        declared_classes.get(&class_hash).is_some(),
                        declared_classes_result.insert(class_hash, contract_class).is_some(),
                    ),
                    ApiContractClass::DeprecatedContractClass(deprecated_contract_class) => (
                        deprecated_declared_classes.contains(&class_hash),
                        deprecated_declared_classes_result
                            .insert(class_hash, deprecated_contract_class)
                            .is_some(),
                    ),
                };
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L130-136)
```rust
    pub fn add_deprecated_class(
        &mut self,
        class_id: ClassId,
        class: RawExecutableClass,
    ) -> ClassManagerResult<()> {
        self.classes.set_deprecated_class(class_id, class)?;
        Ok(())
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L557-568)
```rust
    fn set_deprecated_class(
        &mut self,
        class_id: ClassId,
        class: RawExecutableClass,
    ) -> Result<(), Self::Error> {
        if self.contains_deprecated_class(class_id) {
            return Ok(());
        }

        self.write_deprecated_class_atomically(class_id, class)?;

        Ok(())
```

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L136-140)
```rust
                // TODO(shahak): Verify cairo0 as well after get_class_definition_block_number is
                // fixed.
                ContractClass::V0(deprecated_contract_class) => {
                    Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
                }
```

**File:** crates/apollo_central_sync/src/lib.rs (L517-521)
```rust
                    if class_hash != *expected_class_hash {
                        panic!(
                            "Class hash mismatch. Expected: {expected_class_hash}, got: \
                             {class_hash}."
                        );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/deprecated_compiled_class.cairo (L192-193)
```text
    let (hash) = deprecated_compiled_class_hash{hash_ptr=pedersen_ptr}(compiled_class);
    compiled_class_fact.hash = hash;
```

**File:** crates/apollo_class_manager/src/class_manager_test.rs (L144-150)
```rust
    let deprecated_class_hash = ClassHash(felt!("0x1806"));
    let deprecated_executable_class =
        RawExecutableClass::try_from(ContractClass::V0(DeprecatedContractClass::default()))
            .unwrap();
    class_manager
        .add_deprecated_class(deprecated_class_hash, deprecated_executable_class.clone())
        .unwrap();
```
