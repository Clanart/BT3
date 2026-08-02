## Confirmed on-chain checks

`code::publish_package` (`aptos-move/framework/aptos-framework/sources/code.move:159-231`) only validates: upgrade policy strength, module-name conflicts/coexistence, and dependency policy (`check_dependencies`, `check_upgradability`), then forwards `module_names` (names only, extracted from `pack.modules`) and the raw `code: vector<vector<u8>>` bundle separately to the native `request_publish`/`request_publish_with_allowed_deps`. [1](#0-0) 

The native handler `native_request_publish` (`aptos-move/framework/natives/src/code.rs:284-360`) only records `expected_modules` (names) and the raw bundle bytes for the VM to load/verify — it never touches `pack.modules[i].source`, `pack.source_digest`, or the manifest, and has no mechanism to check that the submitted bytecode was compiled from the source text embedded in the metadata. [2](#0-1) 

`PackageMetadata` (including `source_digest`, `manifest`, and each `ModuleMetadata.source`) is entirely publisher-supplied, BCS-decoded straight from the transaction argument with no cryptographic binding to the compiled bytecode passed in `code`. [3](#0-2) [4](#0-3) 

## Client-side verification gap

`StoredPackage::verify` (`aptos-move/cli/src/stored_package.rs:193-241`) compares `name`, `deps`, `modules` (name+source+source_map descriptors), `manifest`, `upgrade_policy`, `extension`, and `source_digest` between the on-chain metadata and a **freshly-rebuilt local metadata struct**. It never fetches or diffs actual on-chain bytecode. [5](#0-4) 

`VerifyPackage::execute` (`aptos-move/cli/src/commands.rs:2104-2140`) builds the package locally, extracts its metadata via `pack.extract_metadata()`, and calls `package.verify(&compiled_metadata)` — it never invokes `registry.get_bytecode()` (used elsewhere by `DownloadPackage`) to compare against the actually-stored module bytes. [6](#0-5) [7](#0-6) 

## Assessment

`source_digest` is documented in `code.move` as "constructed by first building the sha256 of each individual source ... and sha256 them again" — a purely off-chain, self-reported convention. [8](#0-7) 

Because the publisher freely controls both `pack` (metadata, including `source_digest` and embedded `ModuleMetadata.source`) and `code` (the real bytecode) as independent arguments to `publish_package_txn`, and nothing in the on-chain path (`publish_package`, `check_upgradability`, `check_dependencies`, `native_request_publish`) or the compatibility/module-init checks correlates `source_digest`/`ModuleMetadata.source` to the actual bytecode, a publisher can embed a "clean" source A in the metadata (and a `source_digest` computed from A) while shipping bytecode compiled from a different source B. `VerifyPackage`, which only diffs the two `PackageMetadata` structs (never on-chain bytecode), will report success as long as the locally-built metadata matches the attacker's self-declared metadata — it provides no evidence about the actually-executing code.

This matches the impact-gate category "Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes." It is not blocked by any existing on-chain check, since compatibility/dependency verification operates on bytecode only and never inspects `source_digest`/`ModuleMetadata.source`.

### Title
On-chain `source_digest`/`ModuleMetadata.source` fields are unbound to the actual published bytecode, making `VerifyPackage` a false attestation of code provenance - (File: `aptos-move/cli/src/stored_package.rs`)

### Summary
`code::publish_package` accepts `PackageMetadata` (including `source_digest` and per-module `source` text) and the actual bytecode bundle `code: vector<vector<u8>>` as two independent, publisher-controlled arguments with no on-chain or client-side check binding one to the other.

### Finding Description
- On-chain, `publish_package` (`code.move:159-231`) forwards only module *names* to `request_publish`/`request_publish_with_allowed_deps`; the native handler (`code.rs:284-360`) never inspects `pack.modules[i].source`, `pack.manifest`, or `pack.source_digest`.
- Client-side, `StoredPackage::verify` (`stored_package.rs:193-241`) and `VerifyPackage::execute` (`commands.rs:2104-2140`) compare only metadata-to-metadata (including the embedded source text and digest), never fetching/comparing the real on-chain compiled module bytes (`registry.get_bytecode()`, used only by `DownloadPackage`, is not called here).
- As a result, `source_digest` and the embedded `ModuleMetadata.source` are self-reported, unverified claims about what bytecode was actually deployed; there is no cryptographic or structural binding checked anywhere in the codebase between metadata-declared source and the module bytes that are loaded and executed.

### Impact Explanation
Any consumer who runs `aptos move verify-package` (or reads `source_digest`/embedded source from a `PackageMetadata`) to gain assurance that a deployed package's executing bytecode matches a specific, auditable source tree can be misled: the tool reports "Successfully verified source of package" while the actually-loaded/executed module bytecode may have been compiled from entirely different, unaudited source. This is a genuine mismatch between "verified bytes" (as reported by tooling) and "committed module bytes" (as actually loaded on-chain), undermining source-based trust decisions (e.g., depending on a `compat`/`immutable` package believed to match published source).

### Likelihood Explanation
Trivial to trigger: the publisher (who fully controls their own `publish_package_txn` call) simply supplies a `PackageMetadata` whose `source_digest`/`modules[].source` fields describe source A while `code` is compiled from source B. No special privilege beyond normal publish rights is needed, and nothing in `check_upgradability`, `check_dependencies`, or the native publish path rejects this.

### Recommendation
Either (a) have the on-chain publish path compute/verify a bytecode-based digest rather than trust a source-derived digest, or (b) require `StoredPackage::verify`/`VerifyPackage` to fetch the actual on-chain compiled module bytecode (via the same mechanism used in `DownloadPackage`) and byte-compare it against the locally recompiled modules, in addition to comparing metadata, so verification cannot succeed on descriptor-only agreement.

### Proof of Concept
1. Prepare two source trees, A (benign) and B (malicious), producing different bytecode for module `M`.
2. Build A locally with `BuiltPackage::build_to`, call `extract_metadata()` to get `PackageMetadata` with `source_digest` = sha256-derived-from-A and `modules[0].source` = gzip(A).
3. Compile B separately to get bytecode `code_B: vector<vector<u8>>`.
4. Submit `code::publish_package_txn(owner, bcs(metadata_A), code_B)` — nothing in `publish_package`/`native_request_publish` rejects the mismatch since names in `metadata_A.modules` still match names in `code_B`.
5. Run `aptos move verify-package --account owner` against local source A: `VerifyPackage::execute` (`commands.rs:2104-2140`) rebuilds from A, compares its metadata (including `source_digest`, `modules[].source`) to the on-chain metadata_A, finds them equal, and prints "Successfully verified source of package" — despite the fact that the actually stored/executed bytecode is `code_B`, never verified anywhere against digest A.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L256-261)
```text
    /// Same as `publish_package` but as an entry function which can be called as a transaction. Because
    /// of current restrictions for txn parameters, the metadata needs to be passed in serialized form.
    public entry fun publish_package_txn(owner: &signer, metadata_serialized: vector<u8>, code: vector<vector<u8>>)
    acquires PackageRegistry {
        publish_package(owner, util::from_bytes<PackageMetadata>(metadata_serialized), code)
    }
```

**File:** aptos-move/framework/natives/src/code.rs (L60-71)
```rust
#[derive(Clone, Debug, Serialize, Deserialize, Eq, PartialEq)]
pub struct PackageMetadata {
    pub name: String,
    pub upgrade_policy: UpgradePolicy,
    pub upgrade_number: u64,
    pub source_digest: String,
    #[serde(with = "serde_bytes")]
    pub manifest: Vec<u8>,
    pub modules: Vec<ModuleMetadata>,
    pub deps: Vec<PackageDep>,
    pub extension: Option<Any>,
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

**File:** aptos-move/cli/src/commands.rs (L2035-2064)
```rust
    async fn execute(self) -> CliTypedResult<&'static str> {
        let client = self.rest_options.client(&self.profile_options)?;
        let registry = CachedPackageRegistry::create(client, self.account, self.bytecode).await?;
        let output_dir = dir_default_to_current(self.output_dir)?;

        let package = registry
            .get_package(self.package)
            .await
            .map_err(|s| CliError::CommandArgumentError(s.to_string()))?;
        if package.upgrade_policy() == UpgradePolicy::arbitrary() {
            return Err(CliError::CommandArgumentError(
                "A package with upgrade policy `arbitrary` cannot be downloaded \
                since it is not safe to depend on such packages."
                    .to_owned(),
            ));
        }
        if self.print_metadata {
            println!("{}", package);
        }
        let package_path = output_dir.join(package.name());
        package
            .save_package_to_disk(package_path.as_path())
            .map_err(|e| CliError::UnexpectedError(format!("Failed to save package: {}", e)))?;
        if self.bytecode {
            for module in package.module_names() {
                if let Some(bytecode) = registry.get_bytecode(module).await? {
                    package.save_bytecode_to_disk(package_path.as_path(), module, bytecode)?
                }
            }
        };
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
