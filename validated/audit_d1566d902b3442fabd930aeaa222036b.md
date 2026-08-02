## Finding: `aptos move verify-package` never compares deployed bytecode to the source it claims to verify

### Title
`VerifyPackage`/`CachedPackageMetadata::verify` validates only `PackageMetadata` fields, never the actual on-chain module bytecode, allowing published bytecode to diverge from the "verified" source — (File: `aptos-move/cli/src/stored_package.rs`)

### Summary
The Aptos CLI's package-verification flow (`aptos move verify-package`) is meant to let a third party confirm that the bytecode running on-chain at an address corresponds to a given source tree. In practice, `CachedPackageMetadata::verify` only diff-checks `PackageMetadata` struct fields (name, deps, module metadata, manifest, upgrade policy, extension, source digest) and never fetches or byte-compares the actual deployed Move bytecode against what compiling the local source would produce.

### Finding Description
`code::publish_package_txn` in `aptos-move/framework/aptos-framework/sources/code.move` takes two independent, uncorrelated inputs from the publisher: `metadata_serialized` (containing, among other things, gzipped `source` text per module and a `source_digest`) and `code: vector<vector<u8>>` (the actual bytecode modules to publish/verify/load). [1](#0-0) 

Nothing in this Move code, nor in the native `request_publish_with_allowed_deps`/`request_publish` path, requires that `metadata.modules[i].source` (or the `source_digest`) actually corresponds to the bytecode in `code[i]`. The dependency/allowed-deps check only validates module *names* and address ownership, not source-to-bytecode fidelity — that binding is assumed to be enforced entirely off-chain by tooling.

The off-chain tool that is supposed to provide that guarantee is `aptos move verify-package`, implemented by `VerifyPackage::execute` in `aptos-move/cli/src/commands.rs`, which:
1. Builds the package from a **local** path the caller supplies.
2. Extracts `PackageMetadata` from that local build via `BuiltPackage::extract_metadata`.
3. Fetches the on-chain `PackageRegistry` with `CachedPackageRegistry::create(client, self.account, false)` — note `with_bytecode = false`, so the actual deployed module bytes are **never downloaded**.
4. Calls `package.verify(&compiled_metadata)` and, on success, prints `"Successfully verified source of package"`. [2](#0-1) 

`CachedPackageMetadata::verify` only compares `PackageMetadata` fields to each other (name, deps, modules metadata, manifest, upgrade_policy, extension, source_digest) — it never touches raw bytecode: [3](#0-2) 

`get_bytecode`, the only function in `CachedPackageRegistry` capable of fetching the real deployed module bytes via `get_account_module`, is used exclusively by `DownloadPackage` to save `.mv` files to disk, and is never wired into the `verify` comparison: [4](#0-3) [5](#0-4) 

Because `ModuleMetadata.source`/`source_map` are optional, gzipped, and purely descriptive fields chosen by the publisher at transaction-construction time (see `BuiltPackage::extract_metadata`, which independently derives `modules[i].source` from local source files and `extract_code`/`module_code_iter` which independently serializes bytecode from the compiled units — these two outputs are never cross-checked against each other before being placed into the same publish transaction): [6](#0-5) [7](#0-6) 

a publisher can submit a `publish_package_txn`/`object_code_deployment::publish` transaction where `metadata_serialized` describes one (benign, "auditable") source tree with a matching `source_digest`, while `code` contains arbitrary different bytecode module(s) with the same module names. `check_upgradability`/`check_coexistence` only assert module-name continuity and policy monotonicity, not that the code matches the declared source: [8](#0-7) 

A downstream verifier running `aptos move verify-package --account <addr>` against the "benign" source will rebuild that source locally, get a `PackageMetadata` whose `modules[].source`/`source_digest` match what's on-chain bit-for-bit (since the on-chain metadata literally is that same source, gzipped), pass all checks in `verify()`, and print `"Successfully verified source of package"` — while the bytecode actually executing at that address (and used by the VM/loader) is something else entirely.

### Impact Explanation
This breaks the "mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes" invariant called out in the publish-safety scope. Any user, wallet, explorer, or integrator that relies on `aptos move verify-package` (or equivalent logic built on `CachedPackageRegistry`/`CachedPackageMetadata::verify`) to confirm that deployed code matches a known-good/audited source tree can be misled into trusting malicious bytecode as "verified." This is a code-safety/trust invariant violation with high real-world relevance, since source verification is the primary mechanism third parties use to establish trust in a contract before interacting with or depending on it (e.g., before allowing it as a dependency, or before a user approves a transaction with it).

### Likelihood Explanation
High likelihood of exploitation with no special privilege required: any unprivileged account publishing a package already fully controls both the `metadata_serialized` and `code` arguments to `code::publish_package_txn` / `object_code_deployment::publish`, and nothing on-chain or in the CLI enforces they correspond. The only "defense" would be a verifier independently recompiling and diffing raw bytecode, which the current CLI path explicitly skips (`with_bytecode = false` in `VerifyPackage::execute`, and `get_bytecode` results are never consulted by `verify()`).

### Recommendation
- In `VerifyPackage::execute`, fetch the on-chain package with `with_bytecode = true` and, for each module, byte-compare (or at minimum, `CompiledModule`-normalize and compare) the fetched bytecode against `BuiltPackage::extract_code`/`module_code_iter` output for the locally rebuilt package, in addition to the existing metadata comparison.
- Extend `CachedPackageMetadata::verify` (or add a new verification path) to accept the map of on-chain bytecode and assert it is byte-identical (or semantically identical after normalizing deserialize/reserialize) to what compiling `modules[i].source` would produce, closing the gap between "verified metadata" and "verified bytes."
- Consider clarifying (or renaming) the current metadata-only check to avoid the misleading "Successfully verified source of package" message when only metadata self-consistency, not code correspondence, has been established.

### Proof of Concept
1. Prepare two Move packages, `PackageBenign` (e.g., a trivial module `m::f`) and `PackageMalicious` (same module name/signature but with a hidden backdoor function/behavior), both compiled for the same account/module names.
2. Build `PackageBenign` locally with `BuiltPackage::build` to obtain `metadata_benign = extract_metadata()`.
3. Build `PackageMalicious` locally to obtain `code_malicious = extract_code()`.
4. Submit `code::publish_package_txn(publisher, bcs(metadata_benign), code_malicious)` (or the `object_code_deployment::publish` equivalent) — this succeeds because `code.move`'s `publish_package` never checks that `code_malicious` corresponds to `metadata_benign.modules[].source`.
5. Run `aptos move verify-package --account <publisher_or_object_addr>` pointed at the local `PackageBenign` source tree.
6. Observe `Ok("Successfully verified source of package")` from `VerifyPackage::execute`, even though the bytecode actually installed on-chain (and executed by any caller) is `code_malicious`, not a compilation of the "verified" benign source. `git`/CI evidence: `CachedPackageRegistry::create(client, self.account, false)` at [9](#0-8)  never retrieves bytecode, and `package.verify(&compiled_metadata)` at line 2137 only exercises the metadata-only comparator shown above.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/code.move (L256-261)
```text
    /// Same as `publish_package` but as an entry function which can be called as a transaction. Because
    /// of current restrictions for txn parameters, the metadata needs to be passed in serialized form.
    public entry fun publish_package_txn(owner: &signer, metadata_serialized: vector<u8>, code: vector<vector<u8>>)
    acquires PackageRegistry {
        publish_package(owner, util::from_bytes<PackageMetadata>(metadata_serialized), code)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L266-295)
```text
    /// Checks whether the given package is upgradable, and returns true if a compatibility check is needed.
    fun check_upgradability(
        old_pack: &PackageMetadata, new_pack: &PackageMetadata, new_modules: &vector<String>) {
        assert!(old_pack.upgrade_policy.policy < upgrade_policy_immutable().policy,
            error::invalid_argument(EUPGRADE_IMMUTABLE));
        assert!(can_change_upgrade_policy_to(old_pack.upgrade_policy, new_pack.upgrade_policy),
            error::invalid_argument(EUPGRADE_WEAKER_POLICY));
        let old_modules = get_module_names(old_pack);

        old_modules.for_each_ref(|old_module| {
            assert!(
                vector::contains(new_modules, old_module),
                EMODULE_MISSING
            );
        });
    }

    /// Checks whether a new package with given names can co-exist with old package.
    fun check_coexistence(old_pack: &PackageMetadata, new_modules: &vector<String>) {
        // The modules introduced by each package must not overlap with `names`.
        old_pack.modules.for_each_ref(|old_mod| {
            let old_mod: &ModuleMetadata = old_mod;
            let j = 0;
            while (j < vector::length(new_modules)) {
                let name = vector::borrow(new_modules, j);
                assert!(&old_mod.name != name, error::already_exists(EMODULE_NAME_CLASH));
                j += 1;
            };
        });
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

**File:** aptos-move/framework/src/built_package.rs (L541-549)
```rust
    pub fn extract_code(&self) -> Vec<Vec<u8>> {
        self.package
            .root_modules()
            .map(|unit_with_source| {
                let bytecode_version = self.options.inferred_bytecode_version();
                unit_with_source.unit.serialize(Some(bytecode_version))
            })
            .collect()
    }
```

**File:** aptos-move/framework/src/built_package.rs (L630-666)
```rust
    /// Extracts metadata, as needed for releasing a package, from the built package.
    pub fn extract_metadata(&self) -> anyhow::Result<PackageMetadata> {
        let source_digest = self
            .package
            .compiled_package_info
            .source_digest
            .map(|s| s.to_string())
            .unwrap_or_default();
        let manifest_file = self.package_path.join("Move.toml");
        let manifest = std::fs::read_to_string(manifest_file)?;
        let custom_props = extract_custom_fields(&manifest)?;
        let manifest = zip_metadata_str(&manifest)?;
        let upgrade_policy = if let Some(val) = custom_props.get(UPGRADE_POLICY_CUSTOM_FIELD) {
            str::parse::<UpgradePolicy>(val.as_ref())?
        } else {
            UpgradePolicy::compat()
        };
        let mut modules = vec![];
        for u in self.package.root_modules() {
            let name = u.unit.name().to_string();
            let source = if self.options.with_srcs {
                zip_metadata_str(&std::fs::read_to_string(&u.source_path)?)?
            } else {
                vec![]
            };
            let source_map = if self.options.with_source_maps {
                zip_metadata(&u.unit.serialize_source_map())?
            } else {
                vec![]
            };
            modules.push(ModuleMetadata {
                name,
                source,
                source_map,
                extension: None,
            })
        }
```
