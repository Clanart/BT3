### Title
`aptos move verify-package` never fetches or compares on-chain module bytecode, so it can "verify" a package whose deployed code differs from the audited source — ([File: aptos-move/cli/src/commands.rs])

### Summary
The `VerifyPackage` CLI command is documented as checking "that on-chain bytecode matches a local source tree" [1](#0-0) , but its implementation calls `CachedPackageRegistry::create(client, self.account, false)` with `with_bytecode = false`, meaning it never downloads the actual on-chain module bytecode [2](#0-1) . It only compares `PackageMetadata` fields (name, deps, module *metadata* descriptors, manifest, upgrade policy, extension, and `source_digest`) between the locally-built package and the on-chain registry entry via `CachedPackageMetadata::verify` [3](#0-2) . None of these fields are cryptographically bound to the actual bytecode bundle (`code: vector<vector<u8>>`) that is executed on-chain; `code::publish_package` accepts `pack: PackageMetadata` and `code` as two independently supplied, uncorrelated arguments and never recomputes or checks `source_digest`/`modules[].source` against the compiled `code` bytes [4](#0-3) .

### Finding Description
`source_digest` is documented as "constructed by first building the sha256 of each individual source, ... and sha256 them again" [5](#0-4) , but this digest is computed entirely off-chain by the publisher's tooling and submitted as an arbitrary `String` inside `PackageMetadata`. The on-chain `publish_package` function performs no validation that `source_digest`, `manifest`, or `modules[].source` in `pack` actually correspond to the bytecode in `code` — it only checks module-name membership (`expected_modules`), upgrade/compatibility policy, and dependency permissions [6](#0-5)  and the VM-side `validate_publish_request` similarly checks module names, dependency allow-lists, resource-group/event metadata, and unstable-bytecode rejection, but never ties `source_digest` to `code` [7](#0-6) .

Consequently a publisher can submit a `code` bundle containing arbitrary bytecode alongside metadata (`manifest`, `modules[].source`, `source_digest`) describing a different, benign-looking source tree. A downstream user/auditor running `aptos move verify-package --account <addr>` against that "expected" benign source tree will:
1. Build the benign source locally and compute its `source_digest`.
2. Fetch only the on-chain `PackageMetadata` (not bytecode) via `CachedPackageRegistry::create(..., false)` [8](#0-7) .
3. Compare metadata fields including `source_digest` — which match, because the attacker deliberately set them to match the benign source — and print "Successfully verified source of package" [9](#0-8) .

The actual executing bytecode is never inspected, so the tool gives a false assurance of code-source correspondence. This directly breaks the "mismatch between verified bytes, package metadata... and committed module bytes" invariant called out in the publish gate.

### Impact Explanation
Users, exchanges, or downstream protocols relying on `aptos move verify-package` (or its CLI documentation's claim of bytecode verification) to confirm that a deployed Aptos module matches audited/open-sourced code can be misled into trusting malicious bytecode that was never actually reviewed. This is a code-safety verification bypass with mainnet relevance: it undermines the entire "verified bytes vs metadata vs committed module bytes" invariant the publish gate calls out, and can facilitate supply-chain-style attacks (a malicious package operator publishes one thing, shows a different, benign source as "verified"). This does not require any privileged access — any package owner can exploit it against any verifier who trusts the tool's stated guarantee.

### Likelihood Explanation
High likelihood: no special preconditions, feature flags, or governance actions are required. Any account with normal publish/upgrade rights can submit metadata whose `source_digest`/`modules[].source`/`manifest` describe arbitrary "source" unrelated to the real `code` bytes, since the Move framework layer performs no correlation check. The only thing standing between an attacker and this outcome is a user's decision to run `verify-package` and trust its result — which is exactly the documented purpose of the tool.

### Recommendation
- Change `VerifyPackage::execute` to call `CachedPackageRegistry::create(client, self.account, true)` (fetch bytecode) and additionally compare the fetched on-chain bytecode against the locally compiled bytecode (`pack.extract_code()`), byte-for-byte or via a bytecode hash, not just metadata fields.
- Consider having the on-chain `code::publish_package`/`request_publish` natives (or the framework `code.move`) validate that `source_digest` is derived from the same content used to build `code`, or otherwise clearly document that `source_digest`/`manifest`/`modules[].source` are unauthenticated, advisory-only fields that must never be relied upon for security verification without an accompanying bytecode comparison.
- Update `cli-deploy.md` to accurately describe what `verify-package` checks today, or fix the implementation to match the documented guarantee.

### Proof of Concept
1. Author `pack_real` — a module containing malicious logic (e.g., a backdoored `admin` function).
2. Compile `pack_real`, obtaining its `code: vector<vector<u8>>` bytecode bundle.
3. Author `pack_fake` — an innocuous module with the same module name(s), source structure compatible with `expected_modules` checks.
4. Build `pack_fake` locally to obtain its `PackageMetadata` (`manifest`, `modules[].source`, `source_digest`).
5. Submit an `code_publish_package_txn(metadata_of_pack_fake, code_of_pack_real)` transaction (or via `object_code_deployment::publish`) — the Move framework only checks module names match `expected_modules` from `pack_fake`'s metadata (which can be crafted to name the same modules as `pack_real`) and applies bytecode verifier/compatibility checks unrelated to source parity; it accepts.
6. A third party clones `pack_fake`'s public source repo and runs `aptos move verify-package --account <addr>`.
7. The tool builds `pack_fake` locally, fetches only `PackageRegistry` metadata (no bytecode) from chain, and finds all metadata fields (including `source_digest`) match `pack_fake` — printing "Successfully verified source of package," even though the actually deployed bytecode is `pack_real`'s malicious code.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L12-12)
```markdown
For code review and reproducibility, [`verify-package`](#aptos-move-verify-package) checks that on-chain bytecode matches a local source tree.
```

**File:** aptos-move/cli/src/commands.rs (L2119-2125)
```rust
        // Now pull the compiled package
        let client = self.rest_options.client(&self.profile_options)?;
        let registry = CachedPackageRegistry::create(client, self.account, false).await?;
        let package = registry
            .get_package(pack.name())
            .await
            .map_err(|s| CliError::CommandArgumentError(s.to_string()))?;
```

**File:** aptos-move/cli/src/commands.rs (L2136-2139)
```rust
        // Verify that the source digest matches
        package.verify(&compiled_metadata)?;

        Ok("Successfully verified source of package")
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L36-38)
```text
        /// The source digest of the sources in the package. This is constructed by first building the
        /// sha256 of each individual source, than sorting them alphabetically, and sha256 them again.
        source_digest: String,
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

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1803-1863)
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
    }
```
