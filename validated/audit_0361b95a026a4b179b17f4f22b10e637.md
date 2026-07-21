### Title
Missing Pedersen-hash verification for deprecated Cairo-0 class bodies in P2P sync allows a malicious peer to store wrong bytecode under a valid class hash - (`crates/apollo_p2p_sync/src/client/class.rs`)

---

### Summary

`ClassStreamBuilder::parse_data_for_block` validates that a peer-supplied `class_hash` is a member of `state_diff.deprecated_declared_classes` (the `is_declared` check), but never verifies that the Pedersen hash of the supplied `DeprecatedContractClass` body equals that `class_hash`. The body is forwarded verbatim to `add_deprecated_class(class_hash, body)`, which stores it without any hash check. A malicious peer can therefore bind an arbitrary Cairo-0 bytecode body to any class hash that appears in the already-committed state diff, causing every subsequent `get_executable` call for that hash to return the wrong bytecode.

---

### Finding Description

**Entrypoint – `parse_data_for_block`**

The `is_declared` guard for deprecated classes is:

```rust
ApiContractClass::DeprecatedContractClass(deprecated_contract_class) => (
    deprecated_declared_classes.contains(&class_hash),   // membership only
    deprecated_declared_classes_result
        .insert(class_hash, deprecated_contract_class)
        .is_some(),
),
``` [1](#0-0) 

`deprecated_declared_classes` is a `HashSet<ClassHash>` built from the locally-stored state diff. The check confirms the hash is in the set; it does **not** compute `hash(body)` and compare it to `class_hash`.

**Storage path – `write_to_storage`**

The accepted `(class_hash, body)` pair is forwarded directly:

```rust
for (class_hash, deprecated_class) in self.1 {
    while let Err(err) = class_manager_client
        .add_deprecated_class(class_hash, deprecated_class.clone())
        .await { ... }
}
``` [2](#0-1) 

**Class manager – `add_deprecated_class`**

The class manager performs no hash verification; it calls `set_deprecated_class` directly:

```rust
pub fn add_deprecated_class(
    &mut self,
    class_id: ClassId,
    class: RawExecutableClass,
) -> ClassManagerResult<()> {
    self.classes.set_deprecated_class(class_id, class)?;
    Ok(())
}
``` [3](#0-2) 

Contrast this with `add_class` (Sierra), which **does** compute the hash from the body:

```rust
let sierra_class = SierraContractClass::try_from(&class)?;
let class_hash = sierra_class.calculate_class_hash();
``` [4](#0-3) 

There is even an open TODO acknowledging the missing check for the Sierra path but nothing for the deprecated path:

```
// TODO(shahak): Verify class hash matches class manager response. report if not.
``` [5](#0-4) 

**Storage guard – `set_deprecated_class`**

`CachedClassStorage::set_deprecated_class` skips writing only if the class is already in the **in-memory cache**:

```rust
if self.deprecated_class_cached(class_id) {
    return Ok(());
}
self.storage.set_deprecated_class(class_id, class.clone())?;
``` [6](#0-5) 

`FsClassStorage::set_deprecated_class` adds a disk-existence check, but only after the cache miss:

```rust
if self.contains_deprecated_class(class_id) {
    return Ok(());
}
self.write_deprecated_class_atomically(class_id, class)?;
``` [7](#0-6) 

On a fresh node (or after a cache eviction before the class has been written to disk), neither guard fires, and the wrong body is persisted.

---

### Impact Explanation

Once the wrong body is stored, every call to `get_executable(class_hash_H)` returns the attacker-controlled bytecode. Any transaction that invokes a contract whose class is `H` will execute the wrong Cairo-0 program, producing wrong storage writes, wrong return values, wrong events, and wrong fee accounting. This satisfies the Critical impact criterion: **wrong contract code selected for execution**.

The global state root is **not** directly corrupted by this path (the state diff, which drives the root, already contains the correct `class_hash_H`). However, if the syncing node is also used as a sequencer or for authoritative RPC responses (fee estimation, simulation, tracing), the wrong bytecode produces wrong authoritative values.

---

### Likelihood Explanation

Any peer that the syncing node connects to can trigger this. The attacker only needs to know one `class_hash` that appears in `deprecated_declared_classes` of any upcoming block (publicly visible on-chain). The attack succeeds on the first sync of that block, before any legitimate peer can supply the correct body.

---

### Recommendation

After `parse_data_for_block` collects the body, compute its Pedersen class hash and compare it to the peer-supplied `class_hash` before inserting into `deprecated_declared_classes_result`. Reject the peer with `BadPeerError` on mismatch. Alternatively, perform the check inside `add_deprecated_class` in the class manager (mirroring the `calculate_class_hash()` call already present in `add_class`) and return an error that the P2P sync layer can treat as a bad-peer signal.

---

### Proof of Concept

```
1. Obtain class_hash H from state_diff.deprecated_declared_classes for block N.
2. Construct a DeprecatedContractClass body B' whose Pedersen hash ≠ H
   (e.g., mutate one felt in program.data).
3. Serve (ApiContractClass::DeprecatedContractClass(B'), H) from a malicious peer.
4. parse_data_for_block: deprecated_declared_classes.contains(&H) → true → accepted.
5. write_to_storage: add_deprecated_class(H, B') → set_deprecated_class(H, B') → written to disk.
6. get_executable(H) → returns B'.
7. Any transaction calling a contract with class H executes B' instead of the correct bytecode.
```

### Citations

**File:** crates/apollo_p2p_sync/src/client/class.rs (L39-39)
```rust
                // TODO(shahak): Verify class hash matches class manager response. report if not.
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

**File:** crates/apollo_class_manager/src/class_manager.rs (L65-66)
```rust
        let sierra_class = SierraContractClass::try_from(&class)?;
        let class_hash = sierra_class.calculate_class_hash();
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L129-137)
```rust
    #[instrument(skip(self, class), ret, err)]
    pub fn add_deprecated_class(
        &mut self,
        class_id: ClassId,
        class: RawExecutableClass,
    ) -> ClassManagerResult<()> {
        self.classes.set_deprecated_class(class_id, class)?;
        Ok(())
    }
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L196-200)
```rust
        if self.deprecated_class_cached(class_id) {
            return Ok(());
        }

        self.storage.set_deprecated_class(class_id, class.clone())?;
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L562-566)
```rust
        if self.contains_deprecated_class(class_id) {
            return Ok(());
        }

        self.write_deprecated_class_atomically(class_id, class)?;
```
