## Analysis

Reducing the external report to its core invariant: **a verification/preview function silently trusts an off-chain-computed value instead of deriving it from the authoritative on-chain data it is supposed to represent**, causing the "verified" result to diverge from the real on-chain state.

I generated several Aptos-native publish-path candidates (package dependency wildcard exemption for addresses `0x1`-`0xa`, object-code-deployment address collision via sequence-number reuse, lazy module-init ownership tracking in `init.move`, and the CLI package verification flow) and traced each for an independently provable, unprivileged root cause. The strongest, fully supported one is in the CLI's `verify-package` flow.

### Title
`aptos move verify-package` never compares on-chain module bytecode, only self-declared metadata, allowing malicious bytecode to be falsely certified as matching trusted source - (File: `aptos-move/cli/src/stored_package.rs`)

### Summary
`aptos move verify-package` is documented as building a package locally and verifying "that the on-chain copy matches" [1](#0-0) , and its own help text says it "verifies the bytecode matches a local compilation of the Move code" [2](#0-1) . In reality, `VerifyPackage::execute` fetches the registry with bytecode fetching disabled (`CachedPackageRegistry::create(client, self.account, false)`) and only calls `package.verify(&compiled_metadata)` [3](#0-2) . `CachedPackageMetadata::verify` compares only `PackageMetadata` fields — `name`, `deps`, `modules` (which is `ModuleMetadata`: name/source/source_map/extension, i.e. an author-supplied gzip blob), `manifest`, `upgrade_policy`, `extension`, and `source_digest` — and never touches the actual on-chain compiled module bytes [4](#0-3) .

### Finding Description
There is no on-chain cryptographic binding between a package's declared `source`/`source_digest` metadata and the actual `code: vector<vector<u8>>` bytes accepted by `code::publish_package` [5](#0-4) . The Move module never checks that `pack.modules[i].source` (gzipped source text) or `pack.source_digest` correspond to the bytecode being published — these are opaque, publisher-controlled byte blobs, and `source_digest`'s documented construction ("sha256 of each individual source... then sha256 again") is only enforced client-side, in `BuiltPackage::extract_metadata` [6](#0-5) , not by the framework.

`CachedPackageRegistry::get_bytecode` and the `with_bytecode` fetch path exist and are used by `DownloadPackage --bytecode` [7](#0-6) , proving the CLI has the capability to compare real bytecode — but `VerifyPackage` deliberately passes `false` and skips this entirely [8](#0-7) .

Consequently, an attacker can publish a package whose `PackageMetadata.modules[i].source` / `source_digest` correspond to innocuous, reviewable source code, while the actually-deployed `code` bytes implement arbitrary different logic (bounded only by the bytecode verifier's structural/compatibility rules, not by content). Anyone who runs `aptos move verify-package --account <attacker>` against the claimed innocuous source will see `"Successfully verified source of package"`, even though the live bytecode is unrelated malicious code.

### Impact Explanation
This is a mismatch between "verified bytes," package metadata, and committed module bytes exactly as described in the Publish Pivots. Users, auditors, governance reviewers, or automated tooling that rely on `verify-package` as a code-safety gate before interacting with, depending on, or approving a package are given a false assurance that on-chain code matches known-good source. This can facilitate supply-chain style attacks on mainnet where deployed bytecode silently diverges from its claimed, reviewed source, defeating a documented code-safety verification mechanism. Given `verify-package` is the canonical Aptos tool for confirming deployed code matches source, this is high impact for any workflow (dependency approval, governance script review via the analogous `VerifyProposal`, third-party contract trust) that treats a successful verification as proof of code authenticity.

### Likelihood Explanation
No special privileges are required — any account can publish a package with metadata inconsistent with its bytecode (nothing in `code::publish_package` cross-checks them), and any user can be misled by running the standard, publicly documented `aptos move verify-package` command. This requires no race condition or governance assumption; it is a deterministic gap in the verification logic reachable by ordinary permissionless publish flows.

### Recommendation
Make `VerifyPackage::execute` fetch on-chain bytecode (`with_bytecode = true`, i.e. `CachedPackageRegistry::create(client, self.account, true)`) and, for every module, compare the fetched on-chain bytecode bytes to the freshly-recompiled `pack.extract_code()` output, failing verification on any mismatch — in addition to (not instead of) the existing metadata comparison in `CachedPackageMetadata::verify`. Update the CLI help/docs to accurately describe the check performed, and consider deprecating/renaming the check until the bytecode comparison is restored so downstream tooling doesn't mistakenly treat a metadata-only match as bytecode authenticity.

### Proof of Concept
1. Prepare package A with innocuous source (e.g., a simple `public fun f() {}`), build it, and extract its `PackageMetadata` (`name`, `source`, `source_digest`, etc.) via `BuiltPackage::extract_metadata` [9](#0-8) .
2. Prepare package B with malicious but structurally-compatible bytecode (same module name/public function signatures so it passes the verifier/compatibility checks), and extract its `code: vector<vector<u8>>`.
3. Submit `code::publish_package_txn(owner, bcs(package_A_metadata), package_B_code)` [10](#0-9)  — this succeeds because `publish_package` never validates that `code` corresponds to `pack.modules[i].source`/`source_digest`.
4. Run `aptos move verify-package --account <owner>` pointed at the honest source for package A. `VerifyPackage::execute` builds A locally, fetches the on-chain metadata (matching A) with `with_bytecode=false`, and calls `package.verify(&compiled_metadata)` [3](#0-2) , which succeeds and prints `"Successfully verified source of package"` — despite the deployed bytecode actually being package B's malicious code.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L70-76)
```markdown
## `aptos move verify-package`

Build the package locally and verify that the on-chain copy matches.

```shellscript filename="Terminal"
aptos move verify-package --account 0xABC...123
```
```

**File:** aptos-move/cli/src/commands.rs (L2029-2071)
```rust
#[async_trait]
impl CliCommand<&'static str> for DownloadPackage {
    fn command_name(&self) -> &'static str {
        "DownloadPackage"
    }

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
        println!(
            "Saved package with {} module(s) to `{}`",
            package.module_names().len(),
            package_path.display()
        );
        Ok("Download succeeded")
    }
```

**File:** aptos-move/cli/src/commands.rs (L2074-2078)
```rust
/// Downloads a package and verifies the bytecode
///
/// Downloads the package from onchain and verifies the bytecode matches a local compilation of the Move code
#[derive(Parser)]
pub struct VerifyPackage {
```

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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L256-261)
```text
    /// Same as `publish_package` but as an entry function which can be called as a transaction. Because
    /// of current restrictions for txn parameters, the metadata needs to be passed in serialized form.
    public entry fun publish_package_txn(owner: &signer, metadata_serialized: vector<u8>, code: vector<vector<u8>>)
    acquires PackageRegistry {
        publish_package(owner, util::from_bytes<PackageMetadata>(metadata_serialized), code)
    }
```

**File:** aptos-move/framework/src/built_package.rs (L630-706)
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
        let deps = self
            .package
            .deps_compiled_units
            .iter()
            .flat_map(|(name, unit)| match &unit.unit {
                CompiledUnit::Module(m) => {
                    let package_name = name.as_str().to_string();
                    let account = AccountAddress::new(m.address.into_bytes());

                    Some(PackageDep {
                        account,
                        package_name,
                    })
                },
                CompiledUnit::Script(_) => None,
            })
            .chain(
                self.package
                    .bytecode_deps
                    .iter()
                    .map(|(name, module)| PackageDep {
                        account: NumericalAddress::from_account_address(*module.self_addr())
                            .into_inner(),
                        package_name: name.as_str().to_string(),
                    }),
            )
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        Ok(PackageMetadata {
            name: self.name().to_string(),
            upgrade_policy,
            upgrade_number: 0,
            source_digest,
            manifest,
            modules,
            deps,
            extension: None,
        })
    }
```
