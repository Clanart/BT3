### Title
`aptos move verify-package` reports success without ever comparing on-chain module bytecode to the locally compiled bytecode - ([File: aptos-move/cli/src/commands.rs])

### Summary
The `VerifyPackage` CLI command is documented and named as verifying that on-chain published bytecode matches a local recompilation of the claimed source, but its implementation only compares `PackageMetadata` fields (name, deps, module *source text*, manifest, upgrade policy, extension, source digest) and never fetches or diffs the actual compiled module bytes stored on chain.

### Finding Description
`VerifyPackage::execute` is documented as: "Downloads the package from onchain and verifies the bytecode matches a local compilation of the Move code" [1](#0-0) . It builds the local package, extracts `compiled_metadata`, fetches the on-chain `CachedPackageRegistry` with `with_bytecode = false`, and calls `package.verify(&compiled_metadata)` before printing "Successfully verified source of package" [2](#0-1) .

`CachedPackageRegistry::create` only populates `bytecode` when `with_bytecode` is true [3](#0-2) , and it is invoked with `false` in `VerifyPackage::execute`, so no on-chain module bytecode is ever downloaded during verification [4](#0-3) .

`CachedPackageMetadata::verify` compares only `PackageMetadata` fields: `name`, `deps`, `modules` (which contains `name`, `source`, `source_map`, `extension` — not bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest` [5](#0-4) . `ModuleMetadata.source` is Move source text (optionally attached at publish time), not the compiled module bytes; the actual bytecode is published separately via the `code: vector<vector<u8>>` argument in `code::publish_package_txn`/`publish_package` [6](#0-5)  and stored as raw module bytes in the write set/module storage [7](#0-6) . Nothing in the on-chain publish path (`request_publish`/`request_publish_with_allowed_deps`, `validate_publish_request`) checks that the uploaded bytecode was actually compiled from the source embedded in the metadata `source` field [8](#0-7)  — it only checks module-name registration, native-function restrictions, and (optionally) allowed dependencies.

Consequently, a publisher can attach honest-looking source/metadata (matching a legitimate repository, correct `source_digest`) while submitting a different, malicious bytecode bundle as `code`. Since `verify-package` never downloads or diffs the actual on-chain bytecode against a fresh compilation, it will report success even though the deployed bytecode diverges from the "verified" source.

### Impact Explanation
This breaks the code-safety invariant that "verified bytes, package metadata, dependency declarations, and committed module bytes" must agree. Users, wallets, or auditors relying on `aptos move verify-package` to confirm that an on-chain package matches open, reviewed source code receive a false positive ("Successfully verified source of package") for a package whose actual bytecode differs from the claimed source — enabling silent deployment of malicious logic under the guise of a verified/audited package. This is a high-impact trust/verification bypass in a publish-adjacent tooling path exposed to any Aptos user of the CLI.

### Likelihood Explanation
High likelihood: this is not a rare edge case but the default and only behavior of the command — `with_bytecode: false` is hardcoded in the call site, and no other code path in `VerifyPackage::execute` fetches or compares bytecode. Any package publisher can trivially construct matching metadata/source-digest while shipping different bytecode, since bytecode and metadata are independent arguments to `code_publish_package_txn` with no on-chain cross-check.

### Recommendation
- In `VerifyPackage::execute`, call `CachedPackageRegistry::create(client, self.account, true)` to fetch on-chain bytecode, and for each module compare `registry.get_bytecode(module_name)` against the freshly compiled bytecode from `pack.extract_code()`/`extract_module_code`, failing verification on any mismatch.
- Update `CachedPackageMetadata::verify` (or the CLI flow) to require this bytecode-level equality check as part of "verification," not just metadata equality.

### Proof of Concept
1. Compile and publish a package where `metadata.modules[0].source` and `manifest` correspond to a legitimate, publicly reviewed Move source file, and `source_digest` is computed correctly from that source, via `code::publish_package_txn(metadata_serialized, code)` — but substitute `code` with a different, malicious but structurally compatible bytecode bundle (same module names, satisfies `validate_publish_request`/bytecode verifier).
2. Run `aptos move verify-package --account <addr>` pointing at a local checkout of the "legitimate" source.
3. Observe: the tool builds the local package, extracts metadata, fetches on-chain `PackageRegistry` (metadata only), and `package.verify(&compiled_metadata)` passes because all compared fields (name/deps/module source&source_map/manifest/policy/extension/source_digest) match the legitimate source — despite the actually deployed bytecode being different. The command prints "Successfully verified source of package" even though the deployed bytecode was never inspected.

### Citations

**File:** aptos-move/cli/src/commands.rs (L2074-2077)
```rust
/// Downloads a package and verifies the bytecode
///
/// Downloads the package from onchain and verifies the bytecode matches a local compilation of the Move code
#[derive(Parser)]
```

**File:** aptos-move/cli/src/commands.rs (L2104-2140)
```rust
    async fn execute(self) -> CliTypedResult<&'static str> {
        // First build the package locally to get the package metadata
        let build_options = BuildOptions {
            install_dir: self.move_options.output_dir.clone(),
            bytecode_version: fix_bytecode_version(
                self.move_options.bytecode_version,
                self.move_options.language_version,
            ),
            ..self.included_artifacts.build_options(&self.move_options)?
        };
        let w = self.env.writer();
        let pack = BuiltPackage::build_to(&w, self.move_options.get_package_path()?, build_options)
            .map_err(|e| CliError::MoveCompilationError(format!("{:#}", e)))?;
        let compiled_metadata = pack.extract_metadata()?;

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
    }
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L157-164)
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

**File:** aptos-move/aptos-vm/src/move_vm_ext/write_op_converter.rs (L57-77)
```rust
    pub(crate) fn convert_modules_into_write_ops(
        &self,
        module_storage: &impl AptosModuleStorage,
        verified_module_bundle: impl Iterator<Item = (ModuleId, Bytes)>,
    ) -> PartialVMResult<BTreeMap<StateKey, ModuleWrite<WriteOp>>> {
        let mut writes = BTreeMap::new();
        for (module_id, bytes) in verified_module_bundle {
            let addr = module_id.address();
            let name = module_id.name();

            // INVARIANT:
            //   No need to charge for module metadata access because the write of a module must
            //   have been already charged for when processing module bundle. Here, it is used for
            //   conversion into a write op - if the metadata exists, it is a modification.
            let state_value_metadata =
                module_storage.unmetered_get_module_state_value_metadata(addr, name)?;
            let op = if state_value_metadata.is_some() {
                Op::Modify(bytes)
            } else {
                Op::New(bytes)
            };
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1803-1841)
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
```
