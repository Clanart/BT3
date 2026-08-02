## Title
`aptos move verify-package` never compares downloaded bytecode against source, allowing published module bytecode to silently diverge from the "verified" source metadata - (File: `aptos-move/cli/src/stored_package.rs`)

### Summary
The Aptos analog of the EthAnchor "unupdated exchange rate feeder" bug-class — a value that consumers implicitly trust to reflect reality but is never actually validated against the true, current on-chain state — appears in the code-verification/publish-trust flow rather than in a rate feeder. `code::publish_package` accepts `pack: PackageMetadata` (containing source, source_map, and `source_digest`) and `code: vector<vector<u8>>` (the actual compiled bytecode) as two entirely independent inputs [1](#0-0) . Nothing in `code::publish_package`, in `AptosVM::validate_publish_request`, or in the CLI's `VerifyPackage` command actually recompiles the metadata's source and compares the resulting bytecode byte-for-byte against the bytecode that was actually stored on chain.

### Finding Description
`CachedPackageMetadata::verify` — the function backing `aptos move verify-package`, which is documented as checking "that on-chain bytecode matches a local compilation of the Move code" [2](#0-1)  — only compares `PackageMetadata` fields: `name`, `deps`, `modules` (which is `ModuleMetadata{name, source, source_map, extension}`, i.e. source text, not bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest` [3](#0-2) .

Crucially, `VerifyPackage::execute` fetches the on-chain registry with bytecode fetching disabled:
```rust
let registry = CachedPackageRegistry::create(client, self.account, false).await?;
...
package.verify(&compiled_metadata)?;
``` [4](#0-3) 

`CachedPackageRegistry::create` only downloads per-module bytecode via `get_account_module` when `with_bytecode` is `true` [5](#0-4) ; since `VerifyPackage` passes `false`, the actual on-chain `.mv` bytecode is never retrieved, and `verify()` never touches `self.bytecode` at all — it compares only the human/tool-supplied metadata (source strings, source maps, digests) that a publisher chose to embed. The `source_digest` field itself is attacker-controlled data supplied inside `PackageMetadata` at publish time; the Move VM's `validate_publish_request` only checks module names, native/metadata validation, resource-group/event validation, and allowed-dependency constraints against the actual `CompiledModule`s [6](#0-5)  — it never recompiles the embedded source and checks it produces the submitted bytecode.

Consequently, a publisher can submit `code::publish_package_txn(metadata, code)` where `metadata.modules[i].source` (and `source_digest`) describes benign, auditable Move source, while `code[i]` is a completely different, malicious compiled module (as long as it satisfies compatibility/module-name/dependency checks, which are structural, not semantic). Anyone running `aptos move verify-package` against that address will see "Successfully verified source of package" even though the deployed bytecode does not correspond to the displayed/claimed source.

### Impact Explanation
This is a mismatch between "verified" metadata and the actual committed module bytes that reach protected state mutation (the published module itself becomes executable code at that address). It undermines the entire code-verification/code-review trust model that `aptos move verify-package` is meant to provide for consumers deciding whether to depend on or interact with a package, directly matching the "Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes" impact category. Because packages can hold real user assets and be treated as audited/reviewed once "verified," this can facilitate deploying and legitimizing malicious bytecode under the guise of reviewed source.

### Likelihood Explanation
High. No special privilege is required — any account able to call `code::publish_package_txn` (which is permissionless for any signer, including resource accounts, and object-code deployment) can supply mismatched `metadata`/`code` pairs. The `VerifyPackage` CLI path is used exactly as documented, with `with_bytecode = false` hardcoded, so the flaw triggers on every invocation, not just an edge case.

### Recommendation
- Make `VerifyPackage::execute` fetch on-chain bytecode (`with_bytecode = true`) and add a real bytecode-equality check in `CachedPackageMetadata::verify` (or a new method) that compares each on-chain module's raw bytes to bytecode produced by locally compiling `compiled_metadata`'s source, not just metadata string equality.
- Alternatively/additionally, enforce on-chain (in `code::publish_package` / native `request_publish`) that `source_digest` is a real hash binding the embedded source/source_map to the compiled module bytes, and reject publishes where the digest cannot be validated to correspond to `code`.

### Proof of Concept
1. Compile package `A` with legitimate, reviewable source `m.move`.
2. Build `PackageMetadata` from package `A` (so `modules[0].source` is `A`'s benign source, and `source_digest` matches `A`).
3. Separately compile a malicious module `B` with the exact same module name/signature (to pass `expected_modules`/compatibility checks in `AptosVM::validate_publish_request` [7](#0-6) ).
4. Call `code::publish_package_txn(owner, bcs(metadata_of_A), vec![bytecode_of_B])` [8](#0-7) . This succeeds because nothing checks that `bytecode_of_B` actually results from compiling `metadata_of_A.modules[0].source`.
5. Run `aptos move verify-package --account <addr>` against a local checkout of package `A`. Because `CachedPackageRegistry::create(..., false)` never fetches on-chain bytecode and `verify()` only compares metadata fields (which all match `A`), the command prints "Successfully verified source of package" [9](#0-8) , even though the actually-deployed executable code is `B`, not `A`.

**Uncertainty note:** I could not fully trace whether any other on-chain or off-chain path (e.g., an explorer or indexer) independently cross-checks `source_digest` against actual bytecode outside of the CLI `verify-package` flow; the index may not contain all such consumers. If a background Devin session is desired to confirm there is no other bytecode-binding check across the full repo (including indexer/explorer code, which may not be part of this repo), that would need direct filesystem access to verify exhaustively.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/code.move (L157-165)
```text
    /// Publishes a package at the given signer's address. The caller must provide package metadata describing the
    /// package.
    public fun publish_package(owner: &signer, pack: PackageMetadata, code: vector<vector<u8>>) acquires PackageRegistry {
        // Disallow incompatible upgrade mode. Governance can decide later if this should be reconsidered.
        assert!(
            pack.upgrade_policy.policy > upgrade_policy_arbitrary().policy,
            error::invalid_argument(EINCOMPATIBLE_POLICY_DISABLED),
        );

```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L256-261)
```text
    /// Same as `publish_package` but as an entry function which can be called as a transaction. Because
    /// of current restrictions for txn parameters, the metadata needs to be passed in serialized form.
    public entry fun publish_package_txn(owner: &signer, metadata_serialized: vector<u8>, code: vector<vector<u8>>)
    acquires PackageRegistry {
        publish_package(owner, util::from_bytes<PackageMetadata>(metadata_serialized), code)
    }
```

**File:** third_party/move/documentation/book/src/cli-deploy.md (L12-12)
```markdown
For code review and reproducibility, [`verify-package`](#aptos-move-verify-package) checks that on-chain bytecode matches a local source tree.
```

**File:** aptos-move/cli/src/stored_package.rs (L40-67)
```rust
impl CachedPackageRegistry {
    /// Creates a new registry.
    pub async fn create(
        client: Client,
        addr: AccountAddress,
        with_bytecode: bool,
    ) -> anyhow::Result<Self> {
        // Need to use a different type to deserialize JSON
        let inner = client
            .get_account_resource_bcs::<PackageRegistry>(addr, "0x1::code::PackageRegistry")
            .await?
            .into_inner();
        let mut bytecode = BTreeMap::new();
        if with_bytecode {
            for pack in &inner.packages {
                for module in &pack.modules {
                    let bytes = client
                        .get_account_module(addr, &module.name)
                        .await?
                        .into_inner()
                        .bytecode
                        .0;
                    bytecode.insert(module.name.clone(), bytes);
                }
            }
        }
        Ok(Self { inner, bytecode })
    }
```

**File:** aptos-move/cli/src/stored_package.rs (L193-241)
```rust
    pub fn verify(&self, package_metadata: &PackageMetadata) -> anyhow::Result<()> {
        let self_metadata = self.metadata;

        if self_metadata.name != package_metadata.name {
            bail!(
                "Package name doesn't match {} : {}",
                package_metadata.name,
                self_metadata.name
            )
        } else if self_metadata.deps != package_metadata.deps {
            bail!(
                "Dependencies don't match {:?} : {:?}",
                package_metadata.deps,
                self_metadata.deps
            )
        } else if self_metadata.modules != package_metadata.modules {
            bail!(
                "Modules don't match {:?} : {:?}",
                package_metadata.modules,
                self_metadata.modules
            )
        } else if self_metadata.manifest != package_metadata.manifest {
            bail!(
                "Manifest doesn't match {:?} : {:?}",
                package_metadata.manifest,
                self_metadata.manifest
            )
        } else if self_metadata.upgrade_policy != package_metadata.upgrade_policy {
            bail!(
                "Upgrade policy doesn't match {:?} : {:?}",
                package_metadata.upgrade_policy,
                self_metadata.upgrade_policy
            )
        } else if self_metadata.extension != package_metadata.extension {
            bail!(
                "Extensions doesn't match {:?} : {:?}",
                package_metadata.extension,
                self_metadata.extension
            )
        } else if self_metadata.source_digest != package_metadata.source_digest {
            bail!(
                "Source digests doesn't match {:?} : {:?}",
                package_metadata.source_digest,
                self_metadata.source_digest
            )
        }

        Ok(())
    }
```

**File:** aptos-move/cli/src/commands.rs (L2119-2139)
```rust
        // Now pull the compiled package
        let client = self.rest_options.client(&self.profile_options)?;
        let registry = CachedPackageRegistry::create(client, self.account, false).await?;
        let package = registry
            .get_package(pack.name())
            .await
            .map_err(|s| CliError::CommandArgumentError(s.to_string()))?;

        // We can't check the arbitrary, because it could change on us
        if package.upgrade_policy() == UpgradePolicy::arbitrary() {
            return Err(CliError::CommandArgumentError(
                "A package with upgrade policy `arbitrary` cannot be downloaded \
                since it is not safe to depend on such packages."
                    .to_owned(),
            ));
        }

        // Verify that the source digest matches
        package.verify(&compiled_metadata)?;

        Ok("Successfully verified source of package")
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1803-1862)
```rust
    /// Validate a publish request.
    fn validate_publish_request(
        &self,
        module_storage: &impl AptosModuleStorage,
        traversal_context: &mut TraversalContext,
        gas_meter: &mut impl GasMeter,
        modules: &[CompiledModule],
        mut expected_modules: BTreeSet<String>,
        allowed_deps: Option<BTreeMap<AccountAddress, BTreeSet<String>>>,
    ) -> VMResult<()> {
        self.reject_unstable_bytecode(modules)?;
        native_validation::validate_module_natives(modules)?;

        for m in modules {
            if !expected_modules.remove(m.self_id().name().as_str()) {
                return Err(Self::metadata_validation_error(&format!(
                    "unregistered module: '{}'",
                    m.self_id().name()
                )));
            }
            if let Some(allowed) = &allowed_deps {
                for dep in m.immediate_dependencies() {
                    if !allowed
                        .get(dep.address())
                        .map(|modules| {
                            modules.contains("") || modules.contains(dep.name().as_str())
                        })
                        .unwrap_or(false)
                    {
                        return Err(Self::metadata_validation_error(&format!(
                            "unregistered dependency: '{}'",
                            dep
                        )));
                    }
                }
            }
            verify_module_metadata_for_module_publishing(m, self.features())
                .map_err(|err| Self::metadata_validation_error(&err.to_string()))?;
        }

        resource_groups::validate_resource_groups(
            self.features(),
            module_storage,
            traversal_context,
            gas_meter,
            modules,
        )?;
        event_validation::validate_module_events(
            self.features(),
            module_storage,
            traversal_context,
            modules,
        )?;

        if !expected_modules.is_empty() {
            return Err(Self::metadata_validation_error(
                "not all registered modules published",
            ));
        }
        Ok(())
```
