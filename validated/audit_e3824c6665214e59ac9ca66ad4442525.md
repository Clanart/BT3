### Title
`aptos move verify-package` never compares on-chain module bytecode, only self-reported metadata, allowing bytecode/source mismatch to pass "verification" - (File: aptos-move/cli/src/commands.rs, aptos-move/cli/src/stored_package.rs)

### Summary
The Aptos CLI's `verify-package` command is documented and expected to prove that the bytecode a package owner has published on-chain matches a given local source tree [1](#0-0) . In the implementation, the on-chain `PackageRegistry` is fetched with bytecode downloading disabled, and the actual comparison is delegated to `CachedPackageMetadata::verify`, which only compares `PackageMetadata` struct fields (name, deps, `ModuleMetadata` records, manifest, upgrade policy, extension, source digest) — it never fetches or hashes the real on-chain compiled module bytes.

### Finding Description
`VerifyPackage::execute` builds the registry with `with_bytecode = false`: [2](#0-1) 

`CachedPackageRegistry::create` only populates `self.bytecode` when `with_bytecode` is `true`; here it stays empty: [3](#0-2) 

The actual comparison, `package.verify(&compiled_metadata)`, only checks `PackageMetadata` fields — `name`, `deps`, `modules` (i.e. `ModuleMetadata` records containing zipped `source`/`source_map`/`extension`, not bytecode hashes), `manifest`, `upgrade_policy`, `extension`, `source_digest`: [4](#0-3) 

Crucially, `PackageMetadata` (and the embedded `source`/`source_digest` fields) is arbitrary, publisher-supplied data passed into `code::publish_package_txn` — it is never checked by the on-chain Move code against the actual bytecode bundle. `code::publish_package` in the framework only checks module name-set membership, upgrade-policy monotonicity, and dependency policy relationships; it does not validate that `PackageMetadata.modules[i].source` (or `source_digest`) actually corresponds to the compiled bytecode bytes that were published in the same transaction: [5](#0-4) 

The native publish-validation path (`native_request_publish` / `AptosVM::validate_publish_request`) likewise only checks module names, expected/allowed dependencies, and bytecode-level compatibility/verifier constraints — it never cross-checks the metadata's embedded source text against the bytecode: [6](#0-5) [7](#0-6) 

So the correspondence between "the source code the metadata claims" and "the bytecode actually stored on-chain" is a pure convention, enforced by nobody on-chain and, critically, not independently verified by the client-side `verify-package` tool either, because that tool never downloads or hashes the real module bytecode. Structurally this is the same class of bug as the report: a documented invariant ("`fCashDebtInReserve > 0` events reconcile off-chain accounting" / here: "verify-package checks on-chain bytecode matches source") is silently unsatisfiable/untested in the actual code path due to an implementation gap, producing an incorrect "success" signal that downstream consumers rely on.

### Impact Explanation
`verify-package` is the primary Aptos-native tool users, auditors, and integrators use to confirm that bytecode deployed by a package owner corresponds to a claimed, reviewable source tree before trusting/depending on it (this is explicitly called out as a security consideration for depending on non-immutable packages) [8](#0-7) . Because the check silently degrades to a metadata-field comparison and never touches actual bytecode, a package owner (or anyone who can influence what the publish transaction's `PackageMetadata` argument contains, independent of the real `code` bundle) can publish/upgrade a package whose real bytecode differs from what the embedded, "verified" source claims, while `aptos move verify-package` still reports "Successfully verified source of package". This is a code-safety/trust-boundary violation matching "Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes" in the Publish Impact Gate, with high relevance since it can mislead reviewers/dependents about what code is actually live on mainnet.

### Likelihood Explanation
Likelihood is high in practice: nothing prevents an already-authorized publisher (the account/object owner with legitimate upgrade rights) from submitting a `publish_package_txn`/`upgrade` transaction where the `metadata_serialized` blob's embedded `source`/`source_digest` describes benign code while the accompanying `code: vector<vector<u8>>` bytecode bundle is different/malicious — the framework's `code::publish_package` never cross-validates these against each other, and the CLI's `verify-package` never fetches or hashes real bytecode to catch the discrepancy.

### Recommendation
- Change `VerifyPackage::execute` to construct `CachedPackageRegistry::create(..., with_bytecode = true)` and extend `CachedPackageMetadata::verify` (or a new method) to fetch each module's on-chain bytecode via `get_bytecode`, recompile/reserialize the local module, and byte-for-byte (or normalized-bytecode) compare it against the fetched on-chain bytecode, aborting verification on any mismatch.
- Alternatively/additionally, have the on-chain `code::publish_package` (or the native publish-validation path) bind `source_digest`/`source` to a hash of the actually-published bytecode bundle so that any divergence is detectable without needing full bytecode download, closing the trust gap between what is claimed in `PackageMetadata` and what is committed on-chain.

### Proof of Concept
1. Compile package `A` at address `0xcafe` and publish it with a `PackageMetadata` whose `ModuleMetadata.source`/`source_digest` correspond to some innocuous `m.move` source file, but pass a different, functionally distinct bytecode bundle as the `code` argument to `code::publish_package_txn` (nothing in `code.move`'s `publish_package` or the native `request_publish[_with_allowed_deps]` validates that the two correspond).
2. Run `aptos move verify-package --account 0xcafe` against a local checkout containing the innocuous `m.move` source.
3. Observe that `VerifyPackage::execute` fetches the `PackageRegistry` with `with_bytecode = false` [2](#0-1) , recompiles the local source, and calls `package.verify(&compiled_metadata)`, which only compares `PackageMetadata` struct fields [4](#0-3)  — since the locally recompiled metadata's `modules`/`source_digest`/etc. match the on-chain metadata (both derived from the same claimed source), the command prints `"Successfully verified source of package"` even though the real on-chain bytecode differs from what was "verified".

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L70-83)
```markdown
## `aptos move verify-package`

Build the package locally and verify that the on-chain copy matches.

```shellscript filename="Terminal"
aptos move verify-package --account 0xABC...123
```

| Flag | Meaning |
|---|---|
| `--account <ADDR>` | Address of the on-chain package to verify against. |
| `--included-artifacts <none\|sparse\|all>` | Match what was used at publish time. |

A package published with `upgrade_policy = "arbitrary"` cannot be verified — its content can change at any time, so the verifier refuses to depend on it.
```

**File:** aptos-move/cli/src/commands.rs (L2119-2138)
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

**File:** aptos-move/cli/src/stored_package.rs (L42-67)
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

**File:** aptos-move/framework/natives/src/code.rs (L284-360)
```rust
fn native_request_publish(
    context: &mut SafeNativeContext,
    _ty_args: &[Type],
    mut args: VecDeque<Value>,
) -> SafeNativeResult<SmallVec<[Value; 1]>> {
    debug_assert!(matches!(args.len(), 4 | 5));
    let with_allowed_deps = args.len() == 5;

    context.charge(CODE_REQUEST_PUBLISH_BASE)?;

    let policy = safely_pop_arg!(args, u8);
    let mut code = vec![];
    for module in safely_pop_arg!(args, Vec<Value>) {
        let module_code = module.value_as::<Vec<u8>>()?;

        context.charge(CODE_REQUEST_PUBLISH_PER_BYTE * NumBytes::new(module_code.len() as u64))?;
        code.push(module_code);
    }

    let allowed_deps = if with_allowed_deps {
        let mut allowed_deps: BTreeMap<AccountAddress, BTreeSet<String>> = BTreeMap::new();

        for dep in safely_pop_arg!(args, Vec<Value>) {
            let (account, module_name) = unpack_allowed_dep(dep)?;

            let entry = allowed_deps.entry(account);

            if let Entry::Vacant(_) = &entry {
                // TODO: Is the 32 here supposed to indicate the length of an account address in bytes?
                context.charge(CODE_REQUEST_PUBLISH_PER_BYTE * NumBytes::new(32))?;
            }

            context
                .charge(CODE_REQUEST_PUBLISH_PER_BYTE * NumBytes::new(module_name.len() as u64))?;
            entry.or_default().insert(module_name);
        }

        Some(allowed_deps)
    } else {
        None
    };

    let mut expected_modules = BTreeSet::new();
    for name in safely_pop_arg!(args, Vec<Value>) {
        let str = get_move_string(name)?;

        // TODO(Gas): fine tune the gas formula
        context.charge(CODE_REQUEST_PUBLISH_PER_BYTE * NumBytes::new(str.len() as u64))?;
        expected_modules.insert(str);
    }

    let destination = safely_pop_arg!(args, AccountAddress);

    // Add own modules to allowed deps
    let allowed_deps = allowed_deps.map(|mut allowed| {
        allowed
            .entry(destination)
            .or_default()
            .extend(expected_modules.clone());
        allowed
    });

    let code_context = context.extensions_mut().get_mut::<NativeCodeContext>();
    if code_context.requested_module_bundle.is_some() || !code_context.enabled {
        // Can't request second time or if publish requests are not allowed.
        return Err(SafeNativeError::abort(EALREADY_REQUESTED));
    }
    code_context.requested_module_bundle = Some(PublishRequest {
        destination,
        bundle: ModuleBundle::new(code),
        expected_modules,
        allowed_deps,
        check_compat: policy != ARBITRARY_POLICY,
    });

    Ok(smallvec![])
}
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1804-1865)
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

**File:** third_party/move/documentation/book/src/modules-and-packages.md (L639-660)
```markdown
### Security considerations for dependencies

As mentioned above, even compatible upgrades can have disastrous effects for
applications that depend on the upgraded code. These effects can come from bugs,
but they can also be the result of malicious upgrades. For example,
an upgraded dependency can suddenly make all functions abort, breaking the
operation of your Move code. Alternatively, an upgraded dependency can make
all functions suddenly cost much more gas to execute than before the upgrade.
As a result, dependencies on upgradeable packages need to be handled with care:

- The safest dependency is, of course, an `immutable` package. This guarantees
  that the dependency will never change, including its transitive dependencies.
  In order to update an immutable package, the owner would have to introduce a
  new major version, which is practically like deploying a new, separate
  and independent package. This is because major versioning can be expressed
  only by name (e.g., `module feature_v1` and `module feature_v2`). However,
  not all package owners like to publish their code as `immutable`, because this
  takes away the ability to fix bugs and update the code in place.
- If you have a dependency on a `compatible` package, it is highly
  recommended you know and understand the entity publishing the package.
  The highest level of assurance is when the package is governed by a
  Decentralized Autonomous Organization (DAO) where no single user can initiate
```
