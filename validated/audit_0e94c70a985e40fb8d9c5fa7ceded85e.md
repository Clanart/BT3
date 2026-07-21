The full call chain is now clear. Let me trace every layer:

**Layer 1 — `parse_data_for_block`** (`crates/apollo_p2p_sync/src/client/class.rs`, line 137):
```rust
ApiContractClass::DeprecatedContractClass(deprecated_contract_class) => (
    deprecated_declared_classes.contains(&class_hash),   // membership only
    deprecated_declared_classes_result.insert(class_hash, deprecated_contract_class).is_some(),
),
```
No hash computation. The class body is accepted if `class_hash` is a member of the state diff set.

**Layer 2 — `write_to_storage`** (same file, line 54–55): passes the body directly to `add_deprecated_class(class_hash, deprecated_class)` with no hash check.

**Layer 3 — `ClassManager::add_deprecated_class`** (`crates/apollo_class_manager/src/class_manager.rs`, line 142): calls `self.classes.set_deprecated_class(class_id, class)` — no hash computation.

**Layer 4 — `FsClassStorage::set_deprecated_class`** (`crates/apollo_class_manager/src/class_storage.rs`, line 567): only checks `if self.contains_deprecated_class(class_id) { return Ok(()); }` (file-existence guard), then writes the body atomically. First write wins; no hash verification at any point.

**Contrast with Sierra classes**: `ClassManager::add_class` computes `sierra_class.calculate_class_hash()` and the p2p client even has a TODO: `// TODO(shahak): Verify class hash matches class manager response. report if not.` No equivalent exists for deprecated classes.

**Storage layer acknowledgement** (`crates/apollo_storage/src/class.rs`, lines 6–8):
> "Note that the written classes' hashes should be the same as those declared in the block's state diff and deploy transactions (now deprecated). **This is not validated** but breaking this will cause the DB to be inconsistent."

---

### Title
Missing Deprecated Class Hash Verification in P2P Sync Allows Malicious Peer to Permanently Store Wrong Cairo 0 Bytecode — (`crates/apollo_p2p_sync/src/client/class.rs`)

### Summary
A network peer can send a `DeprecatedContractClass` body that does not hash to the `class_hash` declared in the state diff. The p2p sync client performs only a set-membership check and no hash verification at any layer. The wrong bytecode is written to persistent storage under the correct `class_hash` key and is served to the execution engine for all subsequent Cairo 0 contract calls at that address.

### Finding Description

`ClassStreamBuilder::parse_data_for_block` reads the state diff's `deprecated_declared_classes` as a `HashSet<ClassHash>` and checks only membership: [1](#0-0) 

The collected `(class_hash, deprecated_contract_class)` pairs are forwarded to `write_to_storage`, which calls `add_deprecated_class` in a retry loop with no hash check: [2](#0-1) 

`ClassManager::add_deprecated_class` passes the body straight to storage: [3](#0-2) 

`FsClassStorage::set_deprecated_class` applies only a file-existence guard (first-write-wins) and then writes the body atomically to disk: [4](#0-3) 

The storage layer's own documentation acknowledges the invariant is unenforced: [5](#0-4) 

The `rename_to_persistent_dir` recovery path even comments that it is "safe to remove" an existing directory because "the directory is named by class hash, so an existing directory holds the same class content (content-addressing invariant)" — an invariant that the p2p sync path does not enforce: [6](#0-5) 

### Impact Explanation

Once the wrong bytecode is written under `class_hash H`, every subsequent call to `get_executable(H)` returns the attacker-controlled bytecode. Any Cairo 0 contract deployed at a class address whose class hash is `H` will execute the wrong program. This produces wrong execution results, wrong receipts, wrong events, and wrong state transitions — all under a `class_hash` that is correctly committed in the state diff. The impact maps directly to: **Critical — Wrong compiled class / contract code selected for execution**.

### Likelihood Explanation

The attack requires only that the malicious peer be the first to serve the deprecated class for a given block. Because the file-existence guard makes the first write permanent, a single malicious response is sufficient. Class hashes are public (they appear in the state diff), so the attacker can craft a targeted payload for any specific deprecated class. No special privileges are required beyond being a reachable p2p peer.

### Recommendation

After receiving a `DeprecatedContractClass` from the network, compute its hash using `compute_deprecated_class_hash` (available in `crates/starknet_os/src/hints/hint_implementation/deprecated_compiled_class/class_hash.rs`) and compare it against the expected `class_hash` from the state diff. If the hashes do not match, call `report_peer()` and retry — the same pattern already used for other bad-peer conditions in `parse_data_for_block`. [7](#0-6) 

### Proof of Concept

1. Construct a state diff for block N containing `deprecated_declared_classes = {H}` where `H` is a real deprecated class hash.
2. Craft a `DeprecatedContractClass` with arbitrary (wrong) bytecode — one that does **not** hash to `H`.
3. Send `(DeprecatedContractClass(wrong_body), H)` as the network response to the class query for block N.
4. `parse_data_for_block` accepts it (`H ∈ deprecated_declared_classes` is true).
5. `write_to_storage` calls `add_deprecated_class(H, wrong_body)`.
6. `FsClassStorage::set_deprecated_class` writes `wrong_body` to disk under `H` (file did not exist).
7. Retrieve via `get_executable(H)` — the returned bytecode is `wrong_body`, not the legitimate class.
8. Execute a Cairo 0 contract at class hash `H` — the wrong program runs.

### Citations

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

**File:** crates/apollo_class_manager/src/class_manager.rs (L136-144)
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

**File:** crates/apollo_class_manager/src/class_storage.rs (L468-494)
```rust
    /// Atomically moves the staged class directory into its content-addressed persistent directory.
    ///
    /// The parent directory is guaranteed to exist: `create_tmp_dir` creates it before this is
    /// called. The rename is attempted optimistically; `ENOTEMPTY` means a prior crash left an
    /// orphaned non-empty directory at `persistent_dir`. Since the directory is named by class hash
    /// it holds the same class content — removing it lets the rename proceed and the existence
    /// marker get committed, restoring filesystem/marker consistency. Any other error is surfaced
    /// immediately.
    fn rename_to_persistent_dir(
        &self,
        tmp_dir: PathBuf,
        persistent_dir: PathBuf,
    ) -> FsClassStorageResult<()> {
        if let Err(rename_error) = std::fs::rename(&tmp_dir, &persistent_dir) {
            // POSIX permits both ENOTEMPTY and EEXIST for a non-empty destination directory;
            // different kernels and filesystems can return either. Recover from both.
            if matches!(
                rename_error.kind(),
                std::io::ErrorKind::DirectoryNotEmpty | std::io::ErrorKind::AlreadyExists
            ) {
                warn!(
                    "Recovering orphaned class dir from a prior partial write: {persistent_dir:?}"
                );
                // Safe to remove: the directory is named by class hash, so an existing
                // directory holds the same class content (content-addressing invariant).
                std::fs::remove_dir_all(&persistent_dir)?;
                std::fs::rename(tmp_dir, persistent_dir)?;
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L561-574)
```rust
    #[instrument(skip(self, class), level = "debug", ret, err)]
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
    }
```

**File:** crates/apollo_storage/src/class.rs (L6-8)
```rust
//! Note that the written classes' hashes should be the same as those declared in the block's state
//! diff and deploy transactions (now depreacted). This is not validated but breaking this will
//! cause the DB to be inconsistent.
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
