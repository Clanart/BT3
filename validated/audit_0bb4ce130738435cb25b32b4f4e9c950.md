### Title
`VerifyPackage` CLI command verifies only self-reported `PackageMetadata`, never the actual on-chain bytecode — ([File: aptos-move/cli/src/commands.rs], [File: aptos-move/cli/src/stored_package.rs])

### Summary
Aptos's `code::publish_package` on-chain flow stores two independent things per module: the actual compiled bytecode (`code: vector<vector<u8>>`, verified by the bytecode verifier/loader) and a `ModuleMetadata.source` field (arbitrary, gzipped, publisher-declared source text) that is never checked by the VM against the bytecode it accompanies <cite repo="blackvul/aptos-core--038" path="aptos-move/framework/aptos-framework/sources/code.move" start="55="65" /> [1](#0-0) . The CLI's `aptos move verify-package` command is meant to let a third party confirm that a deployed package's bytecode matches its claimed source. However, it only fetches `PackageMetadata` (`with_bytecode=false`) and compares metadata fields (`name`, `deps`, `modules`, `manifest`, `upgrade_policy`, `source_digest`) between the locally rebuilt package and the on-chain metadata — it never downloads or diffs the actual on-chain bytecode against the locally compiled bytecode [2](#0-1) [3](#0-2) [4](#0-3) . This is structurally the same flaw as the OracleRef bug class: one value (the "source"/metadata) is trusted to describe another independently-supplied value (the "bytecode"/code) without validating the binding between them.

### Finding Description
`publish_package` accepts `pack: PackageMetadata` and `code: vector<vector<u8>>` as two separate, unrelated parameters supplied by the caller in the same transaction [5](#0-4) . Nothing in `code.move`, in the native `request_publish`/`request_publish_with_allowed_deps` calls, or in `aptos_vm.rs`'s `validate_publish_request` ties `ModuleMetadata.source` (or `source_map`) to the bytes in `code` — the metadata's `source` field is opaque gzip data that is stored as-is and only used for display/reconstruction purposes by tooling; the VM's own checks operate solely on the `CompiledModule`s deserialized from `code` (name registration, dependency allow-list, native/module attribute validation) [6](#0-5) .

The CLI `VerifyPackage` command is the tool users are expected to use to gain confidence that on-chain code corresponds to a given source tree (e.g., before adding the package as a dependency or trusting it). It:
1. Rebuilds the package locally from source and computes `compiled_metadata` (including `source_digest`) [7](#0-6) .
2. Fetches the on-chain `PackageMetadata` via `CachedPackageRegistry::create(client, self.account, false)` — the `false` disables bytecode retrieval entirely [8](#0-7) [3](#0-2) .
3. Calls `package.verify(&compiled_metadata)`, which only compares `name`, `deps`, `modules` (source/source_map bytes), `manifest`, `upgrade_policy`, `extension`, and `source_digest` — all self-reported metadata fields — and never fetches or compares actual module bytecode [4](#0-3) .

Because the publisher fully controls both the `PackageMetadata` (including `source`/`source_digest`) and the `code` bytes in the same `publish_package_txn` call, a malicious or compromised publisher can submit metadata whose `source`/`source_digest` corresponds to an honest, publicly reviewed source tree, while submitting different (malicious) compiled bytecode as `code`. `verify-package` will rebuild the same honest source, get a matching `source_digest`/`modules`/`manifest`, and report "Successfully verified source of package" — even though the deployed bytecode is entirely different from what was "verified."

### Impact Explanation
This breaks the metadata/bytecode-consistency invariant required by the "Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes" impact category. Any downstream consumer relying on `aptos move verify-package` (dApp integrators, auditors, users deciding whether to add a package as a dependency, or interact with its entry functions) can be misled into trusting malicious bytecode that was never actually verified. Since Aptos's dependency/upgrade-policy model explicitly encourages depending on `compatible`/`immutable` packages "governed by ... an entity you understand," and recommends verification, this false-positive verification undermines the core trust mechanism intended to protect users from a compromised or malicious package owner deploying harmful code while presenting benign source. The severity is High: it enables users to be tricked into trusting/interacting with arbitrary malicious on-chain code presented as verified benign code, which can directly lead to loss of funds when such code is invoked.

### Likelihood Explanation
Likelihood is high for any scenario where `verify-package` is used as an actual trust gate (its stated purpose), because:
- Nothing on-chain enforces any link between `ModuleMetadata.source`/`source_digest` and the actual `code` bytes — this is a structural gap, not an edge case <cite repo="blackvul/aptos-core--038" path="aptos-move/framework/aptos-framework/sources/code.move" start="27="47" />.
- `CachedPackageRegistry::create` is called with `with_bytecode=false` specifically in the `VerifyPackage` path, so bytecode is never even fetched, let alone compared [8](#0-7) .
- No privileged access or race condition is required; a single malicious `publish_package_txn` with mismatched metadata/code suffices, and the CLI tool's blind spot is deterministic, not probabilistic.

### Recommendation
`verify-package` should call `CachedPackageRegistry::create(client, account, true)` (with bytecode) and additionally fetch each on-chain module's actual bytecode via `get_bytecode`, then re-verify that the locally compiled bytecode (built from the same source used to compute `compiled_metadata`) is byte-for-byte (or semantically/AST) equal to the on-chain bytecode for every module in the package, in addition to the existing metadata-field comparison in `CachedPackageMetadata::verify`. Consider also strengthening the on-chain invariant documentation to make explicit that `ModuleMetadata.source`/`source_digest` are unauthenticated, publisher-supplied annotations with no cryptographic binding to the published bytecode, so integrators do not rely on off-chain tooling assumptions without full bytecode verification.

### Proof of Concept
1. Publisher builds an honest package `P` with module `m.move` (harmless logic), computes its `PackageMetadata` (`source`, `source_digest`, `manifest`, etc.) via the standard build pipeline.
2. Publisher separately compiles a malicious variant `m_evil.move` implementing hostile logic (e.g., draining resources on a call), producing bytecode `code_evil`.
3. Publisher submits `code::publish_package_txn(owner, metadata_of_P_serialized, code_evil)` — the transaction succeeds because nothing checks that `code_evil` corresponds to `metadata_of_P` [9](#0-8) .
4. A user runs `aptos move verify-package --account <publisher> ` against the honest source tree `P`. The CLI rebuilds `P` locally, fetches on-chain `PackageMetadata` (without bytecode), compares metadata fields — all match (since metadata matches `P`) — and prints `"Successfully verified source of package"` [2](#0-1) .
5. The user, believing the deployed module is `P`, interacts with or depends on it, but the actually-executing bytecode is `code_evil`.

Note: I was not able to fully trace the `bytecode.rs`/decompile tooling referenced in the CLI (`aptos-move/cli/src/bytecode.rs`) within the available context to confirm whether any other CLI subcommand independently performs a bytecode-to-source comparison; if such a path exists and is wired into `verify-package`, it would mitigate this finding. Based on the code paths inspected, `VerifyPackage::execute` does not invoke it.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/code.move (L159-231)
```text
    public fun publish_package(owner: &signer, pack: PackageMetadata, code: vector<vector<u8>>) acquires PackageRegistry {
        // Disallow incompatible upgrade mode. Governance can decide later if this should be reconsidered.
        assert!(
            pack.upgrade_policy.policy > upgrade_policy_arbitrary().policy,
            error::invalid_argument(EINCOMPATIBLE_POLICY_DISABLED),
        );

        let addr = signer::address_of(owner);
        if (!exists<PackageRegistry>(addr)) {
            move_to(owner, PackageRegistry { packages: vector::empty() })
        };

        // Checks for valid dependencies to other packages
        let allowed_deps = check_dependencies(addr, &pack);

        // Check package against conflicts
        // To avoid prover compiler error on spec
        // the package need to be an immutable variable
        let module_names = get_module_names(&pack);

        // Record, per module in this package, the object's transitive root owner at (re)publish, so
        // lazy self-init can detect a later transfer of the object or an ancestor since that module
        // was published (see `init::internal_maybe_initialize`). Objects only; feature-gated.
        if (features::is_lazy_module_initialization_enabled() && object::is_object(addr)) {
            let owner = object::address_to_object<object::ObjectCore>(addr).root_owner();
            module_names.for_each_ref(|name| {
                init::record_deploy_owner(addr, *name.bytes(), owner);
            });
        };
        let package_immutable = &borrow_global<PackageRegistry>(addr).packages;
        let len = package_immutable.length();
        let index = len;
        let upgrade_number = 0;
        package_immutable.enumerate_ref(|i, old| {
            let old: &PackageMetadata = old;
            if (old.name == pack.name) {
                upgrade_number = old.upgrade_number + 1;
                check_upgradability(old, &pack, &module_names);
                index = i;
            } else {
                check_coexistence(old, &module_names)
            };
        });

        // Assign the upgrade counter.
        pack.upgrade_number = upgrade_number;

        let packages = &mut borrow_global_mut<PackageRegistry>(addr).packages;
        // Update registry
        let policy = pack.upgrade_policy;
        if (index < len) {
            pack.modules.for_each_ref(|m| {
                let m: &ModuleMetadata = m;
                init::reset_initialized(addr, *m.name.bytes());
            });
            *packages.borrow_mut(index) = pack
        } else {
            packages.push_back(pack)
        };

        event::emit(PublishPackage {
            code_address: addr,
            is_upgrade: upgrade_number > 0
        });

        // Request publish
        if (features::code_dependency_check_enabled())
            request_publish_with_allowed_deps(addr, module_names, allowed_deps, code, policy.policy)
        else
        // The new `request_publish_with_allowed_deps` has not yet rolled out, so call downwards
        // compatible code.
            request_publish(addr, module_names, code, policy.policy)
    }
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

**File:** aptos-move/cli/src/stored_package.rs (L42-66)
```rust
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

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1804-1843)
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
        self.reject_legacy_module_bytecode(modules)?;
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
