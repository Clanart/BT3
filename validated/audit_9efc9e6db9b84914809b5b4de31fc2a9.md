## Finding: `aptos move verify-package` never checks that on-chain bytecode matches locally-compiled bytecode

### Title
Source-code verification bypass: `VerifyPackage`/`CachedPackageRegistry` compares only `PackageMetadata`, never the deployed module bytecode - (File: `aptos-move/cli/src/stored_package.rs`, `aptos-move/cli/src/commands.rs`)

### Summary
The Aptos CLI's `aptos move verify-package` command is meant to prove that the bytecode published on-chain matches a given source tree. In practice it only diffs the on-chain `PackageMetadata` struct (name, deps, module metadata records, manifest, upgrade policy, extension, source digest) against a freshly-built local `PackageMetadata`. It never fetches or compares the actual `MoveModule` bytecode bytes stored on-chain for that account, so a package whose declared metadata matches trusted source can silently run different (malicious) bytecode.

### Finding Description
`VerifyPackage::execute()` builds the local package, extracts its metadata, downloads the remote registry, and calls `package.verify(&compiled_metadata)`: [1](#0-0) 

Crucially, it constructs the remote registry with `with_bytecode = false`: [2](#0-1) 

`CachedPackageRegistry::create` only populates its `bytecode: BTreeMap<...>` cache when `with_bytecode` is `true`; with `false` no module bytecode is ever fetched from chain: [3](#0-2) 

`get_bytecode()` exists on `CachedPackageRegistry` but is unused by the verify flow: [4](#0-3) 

Finally, `CachedPackageMetadata::verify()` compares only metadata fields — name, `deps`, `modules` (module-level metadata: name/source/source_map/extension, not code), `manifest`, `upgrade_policy`, `extension`, `source_digest` — and returns `Ok(())` once all of these match: [5](#0-4) 

On-chain, nothing enforces that the `code: vector<vector<u8>>` argument to `code::publish_package` actually corresponds to the accompanying `PackageMetadata`. `publish_package` (Move) only checks that every compiled module's declared name is in the metadata's `expected_modules` set and that its dependencies are within the declared `allowed_deps`; it performs no correspondence check between the metadata's `source`/`source_digest`/`manifest` fields and the actual bytecode being stored: [6](#0-5) [7](#0-6) 

Consequently a publisher can attach metadata (source, source_map, source_digest, manifest) describing an entirely benign/reviewed package while submitting `code` bytes for a bytecode-different module (e.g. one compiled from modified source with an added backdoor entry function, altered constants, or removed safety checks) as long as module names/signatures still satisfy the on-chain compatibility/dependency checks. `verify-package` will still report "Successfully verified source of package" because it never looks at the deployed bytecode at all.

### Impact Explanation
Source-code verification is the trust anchor many users, wallets, exchanges and auditors rely on before interacting with, approving, or delegating authority to a Move package (`aptos move verify-package` / equivalent explorer "verify source" features consuming this same code path). If the deployed bytecode can silently diverge from the "verified" source while all metadata checks pass, an attacker can present a fully verified/trusted package that actually executes different logic — e.g. mint/withdraw bypasses, hidden privileged functions, or logic that behaves maliciously under specific conditions not present in the reviewed source. This is a direct violation of the "mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes" invariant the publish path is supposed to guarantee, with high real-world impact since it undermines the entire code-verification trust model for any consumer of this CLI/registry code.

### Likelihood Explanation
Likelihood is high for any package owner who controls their own publish transaction (which is the common case — anyone deploying to their own account or object address controls both the `metadata_serialized` and `code` payloads of `code::publish_package_txn` / `object_code_deployment::publish`). No special privilege beyond being package owner is required, and the mismatch is trivial to construct: submit legitimate-looking metadata (matching a public source tree) with different compiled bytecode for the same module names. It only requires that the differing bytecode still passes the loader's compatibility/verifier/dependency checks (a low bar, since these are unrelated to source correspondence).

### Recommendation
`PackageMetadata`/`ModuleMetadata` should carry a content hash (or equivalent commitment) of each module's actual compiled bytecode, and either the on-chain `code::publish_package` should assert `hash(code[i]) == metadata.modules[i].code_hash`, or (at minimum) the CLI's `verify-package`/`CachedPackageRegistry::verify` flow must always fetch on-chain bytecode (`with_bytecode = true`) and byte-compare it against the freshly compiled local bytecode before reporting success, rather than relying solely on metadata field equality.

### Proof of Concept
1. Publish `PackageA` at address `0xcafe` where `metadata_serialized` is built from a benign, public `sources/m.move` that only implements `fun f() {}` and passes `PackageBuilder`/`BuiltPackage::extract_metadata()` unmodified, but where the accompanying `code` vector is separately compiled from a modified version of `m.move` that adds `public entry fun drain(...)` (same public function surface plus one extra entry function, which does not break the metadata comparisons and can be crafted to satisfy compatibility/dependency checks).
2. A third party clones the benign public source, runs `aptos move verify-package --account 0xcafe`.
3. `VerifyPackage::execute()` builds local metadata from the benign source, fetches remote registry with `with_bytecode=false` [8](#0-7) , and calls `package.verify(&compiled_metadata)` which only diffs metadata fields [5](#0-4) ; since metadata was built to match, verification succeeds and prints "Successfully verified source of package" even though the deployed `drain` function does not exist in the reviewed source.

I was not able to run this end-to-end in this environment; the analysis is based on static tracing of `VerifyPackage::execute`, `CachedPackageRegistry::create`/`verify`, and `code::publish_package`, which together confirm no bytecode-to-metadata binding exists anywhere in this path.

### Citations

**File:** aptos-move/cli/src/commands.rs (L2104-2139)
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

**File:** aptos-move/cli/src/stored_package.rs (L108-117)
```rust
    /// Gets the bytecode associated with the module.
    pub async fn get_bytecode(
        &self,
        module_name: impl AsRef<str>,
    ) -> anyhow::Result<Option<&[u8]>> {
        Ok(self
            .bytecode
            .get(module_name.as_ref())
            .map(|v| v.as_slice()))
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
