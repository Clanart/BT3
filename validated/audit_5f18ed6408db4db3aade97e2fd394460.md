## Finding: `aptos move verify-package` never compares on-chain bytecode to locally compiled bytecode

### Title
`VerifyPackage` CLI command certifies "verified" packages without ever inspecting the deployed module bytecode - ([File: aptos-move/cli/src/commands.rs])

### Summary
The `aptos move verify-package` CLI subcommand is meant to give users/auditors assurance that the bytecode published on-chain at a given address matches a local, re-buildable source tree. In practice, `VerifyPackage::execute` fetches the on-chain `PackageRegistry` **without downloading any module bytecode**, and the subsequent `verify()` call only diffs self-reported metadata fields — never the actual `.mv` bytes that the VM will execute.

### Finding Description
`VerifyPackage::execute` builds the local package, extracts its metadata, then creates a `CachedPackageRegistry` with bytecode fetching explicitly disabled: [1](#0-0) 

`CachedPackageRegistry::create` only populates the `bytecode: BTreeMap` when `with_bytecode` is `true`; here it's `false`, so `self.bytecode` remains empty: [2](#0-1) 

`CachedPackageMetadata::verify` then compares only the following fields against the locally compiled metadata: `name`, `deps`, `modules` (a `ModuleMetadata` struct that stores only `name`, gzipped `source`, gzipped `source_map`, `extension` — not bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest`: [3](#0-2) 

None of these fields are the actual compiled module bytes stored in the `AccountModule` under `0x1::code::PackageRegistry` (fetched separately via `get_account_module`, only used by `get_bytecode`, which `VerifyPackage` never calls). `source_digest` and `manifest`/`modules[].source` are self-reported by the publisher's `PackageMetadata` transaction argument — nothing in `code::publish_package` (Move) or the native publish path cryptographically ties `source_digest`/`manifest`/`ModuleMetadata.source` to the actual bytecode that got verified and stored; those are opaque, publisher-supplied blobs: [4](#0-3) 

The bytecode verifier/compatibility checks (`validate_publish_request`, `Compatibility::check`) operate purely on the module bytes and never validate them against the declared `source`/`source_digest` metadata — that link is assumed to be true, not enforced: [5](#0-4) 

As a result, a publisher can submit metadata (`manifest`, `modules[].source`, `source_digest`) describing one (benign) source tree while actually publishing different, malicious bytecode in the same transaction (the on-chain code storage and the metadata are independent transaction arguments). `aptos move verify-package` will report "Successfully verified source of package" because it never looks at the real bytecode.

### Impact Explanation
This is a metadata/bytecode integrity mismatch in the publish-verification tooling: the exact invariant broken is "verified bytes must match committed module bytes." Any downstream consumer (wallet, auditor, dependency reviewer, dApp integrator, or another Move package publisher relying on `is_policy_exempted_address`/audited immutable dependencies) that uses `aptos move verify-package` to gain assurance that on-chain code matches published/audited source can be silently deceived into trusting malicious bytecode that was never actually inspected. Given code.move's compatibility/dependency-policy design explicitly recommends relying on this kind of verification for trusting `compatible`/`immutable` dependencies, this undermines a core supply-chain trust mechanism for permissionless publish/upgrade flows on mainnet.

### Likelihood Explanation
High likelihood of being hit in practice: this is the documented, first-class verification workflow (`VerifyPackage` command, module doc "Downloads a package and verifies the bytecode... verifies the bytecode matches a local compilation"), yet by construction it always calls `CachedPackageRegistry::create(..., false)`, so bytecode is never available to compare. No attacker action beyond normal permissionless publish is required — publishing code whose metadata doesn't match the actual bytecode is not blocked by any on-chain check.

### Recommendation
- In `VerifyPackage::execute`, call `CachedPackageRegistry::create(client, self.account, true)` to fetch on-chain bytecode for every module.
- Extend `CachedPackageMetadata::verify` (or add a new check) to recompile the local package to bytecode and byte-for-byte compare each module's on-chain bytes (via `get_bytecode`) against the locally compiled `.mv` bytes, failing verification on any mismatch.
- Consider deriving/checking `source_digest` from the actual compiled bytecode (or binding it cryptographically) rather than trusting it as an independent publisher-supplied field.

### Proof of Concept
1. Publish package `P` at address `0xcafe` via `publish_package_txn`, providing `PackageMetadata` whose `manifest`/`modules[].source`/`source_digest` describe an innocuous module `m` performing `public fun f() {}`, but pass a `code: vector<vector<u8>>` bundle containing a different compiled module `m` (e.g., with backdoored logic), still named `m` and self-addressed to `0xcafe::m` so it passes `addr != sender` and `expected_modules` checks in `validate_publish_request`.
2. Run `aptos move verify-package --account 0xcafe` pointing `move_options` at the innocuous local source used to produce the metadata.
3. Observe the command prints `"Successfully verified source of package"` even though the deployed bytecode differs entirely from the verified/audited source, because `CachedPackageRegistry::create(..., false)` never fetched bytecode and `verify()` never compared it. [1](#0-0) [3](#0-2)

### Citations

**File:** aptos-move/cli/src/commands.rs (L2119-2137)
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L157-231)
```text
    /// Publishes a package at the given signer's address. The caller must provide package metadata describing the
    /// package.
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
