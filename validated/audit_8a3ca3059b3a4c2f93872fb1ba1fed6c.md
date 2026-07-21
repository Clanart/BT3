### Title
Missing Deprecated Class Content Hash Verification in P2P Sync Allows Malicious Peer to Corrupt Cairo 0 Bytecode — (`crates/apollo_p2p_sync/src/client/class.rs`)

---

### Summary

`ClassStreamBuilder::parse_data_for_block` accepts a `DeprecatedContractClass` body from a peer solely by checking that the accompanying `class_hash` appears in the locally-stored state diff's `deprecated_declared_classes` set. It never computes `compute_deprecated_class_hash` over the received body and never compares the result to `class_hash`. The class body is forwarded verbatim through `write_to_storage` → `add_deprecated_class` → `set_deprecated_class` and persisted. Any contract whose class hash maps to that entry will subsequently execute the attacker-supplied bytecode.

---

### Finding Description

**Admission check (the only guard):**

In `parse_data_for_block`, the `DeprecatedContractClass` arm performs exactly one membership test:

```rust
ApiContractClass::DeprecatedContractClass(deprecated_contract_class) => (
    deprecated_declared_classes.contains(&class_hash),   // line 137 — only guard
    deprecated_declared_classes_result
        .insert(class_hash, deprecated_contract_class)
        .is_some(),
),
``` [1](#0-0) 

No hash is computed over `deprecated_contract_class`; the body is inserted directly into `deprecated_declared_classes_result`.

**Contrast with Sierra classes:** `ClassManager::add_class` immediately derives the class hash from the content via `sierra_class.calculate_class_hash()` and returns it so the caller can verify it. There is even an explicit TODO to enforce this check:

```rust
// TODO(shahak): Verify class hash matches class manager response. report if not.
while let Err(err) = class_manager_client.add_class(class.clone()).await { … }
``` [2](#0-1) [3](#0-2) 

No equivalent computation or TODO exists for the deprecated path.

**Storage path — no verification at any layer:**

`write_to_storage` calls `add_deprecated_class` unconditionally:

```rust
for (class_hash, deprecated_class) in self.1 {
    while let Err(err) = class_manager_client
        .add_deprecated_class(class_hash, deprecated_class.clone())
        .await { … }
}
``` [4](#0-3) 

`ClassManager::add_deprecated_class` passes straight through to storage:

```rust
pub fn add_deprecated_class(
    &mut self, class_id: ClassId, class: RawExecutableClass,
) -> ClassManagerResult<()> {
    self.classes.set_deprecated_class(class_id, class)?;
    Ok(())
}
``` [5](#0-4) 

`CachedClassStorage::set_deprecated_class` has one guard — a cache-hit early return — but if the class has not been seen before (first-time sync), it writes the attacker-supplied bytes directly:

```rust
fn set_deprecated_class(&mut self, class_id: ClassId, class: RawExecutableClass) -> … {
    if self.deprecated_class_cached(class_id) { return Ok(()); }
    self.storage.set_deprecated_class(class_id, class.clone())?;
    …
    self.deprecated_classes.set(class_id, class);
    Ok(())
}
``` [6](#0-5) 

**The hash computation exists but is never called in the sync path.** `compute_deprecated_class_hash` is implemented in the OS hints crate and is used during OS proof execution to verify deprecated class facts, but it is never invoked during p2p class ingestion: [7](#0-6) 

---

### Impact Explanation

A malicious peer that responds to a class query for block `N` can supply an `ApiContractClass::DeprecatedContractClass` with arbitrary bytecode under any `class_hash H` that legitimately appears in block `N`'s `deprecated_declared_classes`. The node stores the wrong bytecode under `H`. Every subsequent execution of a contract whose class hash is `H` will run the attacker-supplied Cairo 0 bytecode instead of the on-chain class. This satisfies the Critical impact criterion: **wrong compiled class / CASM artifact selected for execution**.

---

### Likelihood Explanation

Any peer that participates in the p2p class sync protocol can trigger this. No operator or validator privilege is required. The window is every block that declares at least one deprecated class. The attack is silent — no error is logged, no peer is reported, and the node continues operating normally with corrupted class storage.

---

### Recommendation

After receiving a `DeprecatedContractClass` body from a peer, compute its hash using `compute_deprecated_class_hash` (already available in the codebase) and compare it to the `class_hash` supplied by the peer. If they differ, treat the peer as bad (`BadPeerError`) and retry. This mirrors the existing (though still TODO) verification for Sierra classes via `add_class`'s returned `ClassHashes`.

---

### Proof of Concept

```
Precondition:
  - Local storage contains a committed ThinStateDiff for block N with
    deprecated_declared_classes = [H], where H is the on-chain class hash
    of some Cairo 0 contract C.

Attack:
  1. Malicious peer responds to the class query for block N.
  2. It sends: (ApiContractClass::DeprecatedContractClass(evil_bytecode), H)
  3. parse_data_for_block checks: deprecated_declared_classes.contains(&H) → true.
     No hash of evil_bytecode is computed. evil_bytecode is inserted into
     deprecated_declared_classes_result under H.
  4. write_to_storage calls add_deprecated_class(H, evil_bytecode).
  5. ClassManager::add_deprecated_class → CachedClassStorage::set_deprecated_class
     stores evil_bytecode under H (cache miss on first sync).

Outcome:
  - get_executable(H) now returns evil_bytecode.
  - Any transaction invoking a contract deployed with class hash H executes
    evil_bytecode instead of the legitimate Cairo 0 class declared on-chain.
  - The corruption is permanent until the node is re-synced from an honest source.
```

### Citations

**File:** crates/apollo_p2p_sync/src/client/class.rs (L38-48)
```rust
                // TODO(shahak): Test this flow.
                // TODO(shahak): Verify class hash matches class manager response. report if not.
                // TODO(shahak): Try to avoid cloning. See if ClientError can contain the request.
                while let Err(err) = class_manager_client.add_class(class.clone()).await {
                    warn!(
                        "Failed writing class with hash {class_hash:?} to class manager. Trying \
                         again. Error: {err:?}"
                    );
                    trace!("Class: {class:?}");
                    // TODO(shahak): Consider sleeping here.
                }
```

**File:** crates/apollo_p2p_sync/src/client/class.rs (L51-65)
```rust
            for (class_hash, deprecated_class) in self.1 {
                // TODO(shahak): Test this flow.
                // TODO(shahak): Try to avoid cloning. See if ClientError can contain the request.
                while let Err(err) = class_manager_client
                    .add_deprecated_class(class_hash, deprecated_class.clone())
                    .await
                {
                    warn!(
                        "Failed writing deprecated class with hash {class_hash:?} to class \
                         manager. Trying again. Error: {err:?}"
                    );
                    trace!("Class: {deprecated_class:?}");
                    // TODO(shahak): Consider sleeping here.
                }
            }
```

**File:** crates/apollo_p2p_sync/src/client/class.rs (L136-141)
```rust
                    ApiContractClass::DeprecatedContractClass(deprecated_contract_class) => (
                        deprecated_declared_classes.contains(&class_hash),
                        deprecated_declared_classes_result
                            .insert(class_hash, deprecated_contract_class)
                            .is_some(),
                    ),
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L64-66)
```rust
    pub async fn add_class(&mut self, class: RawClass) -> ClassManagerResult<ClassHashes> {
        let sierra_class = SierraContractClass::try_from(&class)?;
        let class_hash = sierra_class.calculate_class_hash();
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L130-137)
```rust
    pub fn add_deprecated_class(
        &mut self,
        class_id: ClassId,
        class: RawExecutableClass,
    ) -> ClassManagerResult<()> {
        self.classes.set_deprecated_class(class_id, class)?;
        Ok(())
    }
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L191-207)
```rust
    fn set_deprecated_class(
        &mut self,
        class_id: ClassId,
        class: RawExecutableClass,
    ) -> Result<(), Self::Error> {
        if self.deprecated_class_cached(class_id) {
            return Ok(());
        }

        self.storage.set_deprecated_class(class_id, class.clone())?;

        increment_n_classes(CairoClassType::Deprecated);
        record_class_size(ClassObjectType::DeprecatedCasm, &class);

        self.deprecated_classes.set(class_id, class);

        Ok(())
```

**File:** crates/starknet_os/src/hints/hint_implementation/deprecated_compiled_class/class_hash.rs (L80-101)
```rust
pub fn compute_deprecated_class_hash(
    contract_class: &ContractClass,
) -> Result<Felt, HintedClassHashError> {
    let hinted_class_hash = compute_cairo_hinted_class_hash(contract_class)?;
    let contract_definition_vec = serde_json::to_vec(contract_class)?;
    let contract_definition: CairoContractDefinition<'_> =
        serde_json::from_slice(&contract_definition_vec)?;

    let FlatEntryPointFelts { external, l1_handler, constructor } =
        get_flat_entry_point_felts(&contract_definition.entry_points_by_type);
    let builtins = ascii_strs_as_felts(&contract_definition.program.builtins);
    let bytecode = hex_strs_as_felts(&contract_definition.program.data);

    let mut hash_state = HashState::<Pedersen>::new();
    hash_state.update_single(&DEPRECATED_COMPILED_CLASS_VERSION);
    hash_state.update_with_hashchain(&external);
    hash_state.update_with_hashchain(&l1_handler);
    hash_state.update_with_hashchain(&constructor);
    hash_state.update_with_hashchain(&builtins);
    hash_state.update_single(&hinted_class_hash);
    hash_state.update_with_hashchain(&bytecode);
    Ok(hash_state.finalize())
```
