### Title
`aptos move verify-package` never fetches or compares on-chain bytecode, so it cannot detect a divergence between published module bytes and the audited source - ([File: aptos-move/cli/src/stored_package.rs])

### Summary
The Aptos CLI's `verify-package` command is documented as checking "that on-chain bytecode matches a local compilation of the Move code" [1](#0-0) . In reality, `VerifyPackage::execute` builds the `CachedPackageRegistry` with `with_bytecode = false`, so raw module bytecode is never downloaded from chain, and `CachedPackageMetadata::verify` only diff-checks fields of `PackageMetadata` (name, deps, `modules` metadata records, manifest, upgrade policy, extension, source_digest) — it never compares actual compiled module bytes.

### Finding Description
`VerifyPackage::execute` creates the registry without bytecode: [2](#0-1) 

`CachedPackageRegistry::create` only populates `self.bytecode` when `with_bytecode` is true: [3](#0-2) 

Since `with_bytecode=false` is hardcoded in the verify flow, `self.bytecode` stays empty and is never consulted by `verify()`. The actual comparison performed is field-by-field on `PackageMetadata`: [4](#0-3) 

Critically, `ModuleMetadata` (the `modules` field being compared) contains only `name`, gzipped `source`, `source_map`, and `extension` — it has **no field that binds it to the actual compiled bytecode bytes** that were published on-chain: [5](#0-4) 

On the publish side, `code::publish_package_txn` / `publish_package` accepts `metadata: PackageMetadata` and `code: vector<vector<u8>>` as two entirely independent parameters — there is no on-chain check that the module bytecode in `code` was actually compiled from the source recorded in `pack.modules[i].source`, nor any hash binding the two: [6](#0-5) [7](#0-6) 

Also, `source_digest` in `PackageMetadata` is computed locally by the Move package tool from the source files at build time (comment: "constructed by first building the sha256 of each individual source..."), and is simply carried as an opaque self-reported field in metadata — the VM/native `request_publish`/`request_publish_with_allowed_deps` only validate compatibility, dependency policy, and complexity of the actual bytecode module, never that the bytecode corresponds to `source_digest` or `modules[i].source`: [8](#0-7) [9](#0-8) 

As a result, a publisher can submit a `publish_package_txn` transaction where `metadata_serialized` (with `modules[i].source` set to legitimate-looking, auditable source, and a `source_digest` consistent with it) diverges completely from the actual `code` bytes deployed (which may contain a backdoor, extra function, or different logic that still satisfies the bytecode verifier and compatibility checker, since those operate purely on the module's structural/type signature, not on any correspondence to the source text). Anyone who later runs `aptos move verify-package` locally recompiles the claimed source, extracts its own `PackageMetadata` (which similarly has no bytecode hash), and compares that against the on-chain `PackageMetadata` fields — which match by construction, since the source published in metadata is exactly what the auditor recompiles. The command reports "Successfully verified source of package" even though the bytecode actually executing on-chain differs from that source.

### Impact Explanation
This breaks the trust boundary the tool explicitly promises ("checks that on-chain bytecode matches a local compilation of the Move code"). Downstream consumers, auditors, and integrators who rely on `aptos move verify-package` (or the underlying `CachedPackageRegistry::verify` API) to confirm that a deployed module's logic matches published/audited source will receive a false positive. This directly maps to the "Publish Impact Gate" criterion: "Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes." An attacker (or a compromised/malicious upgrade authority under a `compatible` policy) can publish arbitrary bytecode logic that is compatible at the structural level yet behaviorally different from the source claimed in metadata, while the standard verification tooling falsely attests correctness. This is a High severity code-integrity/trust issue since it undermines the core guarantee that "verified" on-chain code matches its claimed source, which is a primary safeguard against malicious code replacement in the publish path.

### Likelihood Explanation
Likelihood is high for any scenario where `verify-package` is used as the sole attestation mechanism (e.g., third-party integrators, wallets, or explorers verifying a contract before trusting it), because:
1. Nothing in the publish path enforces bytecode-to-source correspondence — it's purely optional/informational metadata.
2. The `verify-package` implementation has a straightforward, easily reproducible gap: it always constructs the registry with `with_bytecode = false`.
3. No cryptographic or structural check ties `ModuleMetadata.source` (or `source_digest`) to the actual on-chain module bytes at any point in the VM's publish flow.

### Recommendation
1. In `VerifyPackage::execute` (`aptos-move/cli/src/commands.rs`), create the `CachedPackageRegistry` with `with_bytecode = true` and fetch each module's on-chain bytecode.
2. In `CachedPackageRegistry::verify` (`aptos-move/cli/src/stored_package.rs`), additionally recompile the local package to bytecode (`BuiltPackage::extract_code` / `module_code_iter`) and byte-for-byte compare it against the fetched on-chain bytecode for every module, failing verification on any mismatch.
3. Consider adding an on-chain enforced binding (e.g., a hash of each module's bytecode recorded in `ModuleMetadata`, checked by `request_publish_with_allowed_deps` against the actual bundle) so that `source_digest`/`modules[i].source` cannot diverge from the executed code without the transaction aborting.

### Proof of Concept
1. Compile a benign package `Foo` with `module Foo { public fun get_value(): u64 { 1 } }`, extract its `PackageMetadata` via `BuiltPackage::extract_metadata()` (this contains the gzipped benign source and a `source_digest` derived from it).
2. Independently compile a malicious bytecode module with the *same* module name/signature but injected malicious logic (e.g., `module Foo { public fun get_value(): u64 { back_door(); 1 } }`), producing `code2: Vec<Vec<u8>>` that still satisfies the bytecode verifier and (if upgrading) the compatibility checker (same public signatures).
3. Submit `code::publish_package_txn(owner, metadata_from_step_1, code2)` — this succeeds because `publish_package` never checks that `code2` corresponds to `metadata_from_step_1.modules[i].source` [6](#0-5) .
4. Run `aptos move verify-package --account <addr>` with the *original benign* local source from step 1. The command builds `CachedPackageRegistry::create(client, addr, false)` [10](#0-9) , never touching bytecode, then calls `package.verify(&compiled_metadata)` [11](#0-10)  which only compares `PackageMetadata` fields — all of which match (since the malicious publish reused the benign metadata) — and prints "Successfully verified source of package" despite the deployed bytecode being different/malicious.

Note: I was unable to execute this against a live devnet/mainnet node within this session; the finding is based on static code-path analysis of the CLI/verification logic and the `code.move`/native publish flow. A live end-to-end reproduction (deploy divergent bytecode + run `verify-package`) would be advisable to confirm the exact CLI output text on the current binary version.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L12-12)
```markdown
For code review and reproducibility, [`verify-package`](#aptos-move-verify-package) checks that on-chain bytecode matches a local source tree.
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L36-38)
```text
        /// The source digest of the sources in the package. This is constructed by first building the
        /// sha256 of each individual source, than sorting them alphabetically, and sha256 them again.
        source_digest: String,
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L55-65)
```text
    /// Metadata about a module in a package.
    struct ModuleMetadata has copy, drop, store {
        /// Name of the module.
        name: String,
        /// Source text, gzipped String. Empty if not provided.
        source: vector<u8>,
        /// Source map, in compressed BCS. Empty if not provided.
        source_map: vector<u8>,
        /// For future extensions.
        extension: Option<Any>,
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L256-261)
```text
    /// Same as `publish_package` but as an entry function which can be called as a transaction. Because
    /// of current restrictions for txn parameters, the metadata needs to be passed in serialized form.
    public entry fun publish_package_txn(owner: &signer, metadata_serialized: vector<u8>, code: vector<vector<u8>>)
    acquires PackageRegistry {
        publish_package(owner, util::from_bytes<PackageMetadata>(metadata_serialized), code)
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
