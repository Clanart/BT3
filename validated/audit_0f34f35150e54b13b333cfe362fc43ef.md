## Finding: `aptos move verify-package` never verifies on-chain bytecode, only compares self-reported metadata fields

### Title
`VerifyPackage` CLI command falsely reports "verified" packages without ever comparing on-chain module bytecode - ([File: aptos-move/cli/src/commands.rs])

### Summary
The `aptos move verify-package` CLI command is documented to "download the package from onchain and verify the bytecode matches a local compilation of the Move code," but its implementation never fetches or compares the actual on-chain compiled module bytes. It only compares two `PackageMetadata` structs — one built locally, one read from the on-chain `PackageRegistry` resource — and both objects consist of self-reported fields (`name`, `deps`, `modules[].name/source`, `manifest`, `source_digest`, etc.) that are never cryptographically bound to the actually-published module bytecode by the Aptos VM at publish time.

### Finding Description
`VerifyPackage::execute` in [1](#0-0)  calls `CachedPackageRegistry::create(client, self.account, false)` with `with_bytecode = false`, which means the on-chain module bytecode is never downloaded at all: `CachedPackageRegistry::create` only populates the `bytecode` map when `with_bytecode` is `true` [2](#0-1) .

The actual comparison is done in `CachedPackageMetadata::verify`, which only diffs metadata fields (`name`, `deps`, `modules`, `manifest`, `upgrade_policy`, `extension`, `source_digest`) between the on-chain `PackageMetadata` and a freshly-built local `PackageMetadata` [3](#0-2) . None of these fields are the compiled bytecode itself; `source_digest` is computed from source files (`self.package.compiled_package_info.source_digest`), not from the published module bytes, when the package is built via `BuiltPackage::extract_metadata` [4](#0-3) .

Crucially, the Aptos on-chain publish flow (`code::publish_package`) never checks that `PackageMetadata.source`, `.manifest`, or `.source_digest` correspond to the actual `code: vector<vector<u8>>` bundle being published — the native/VM-side validation in `validate_publish_request` only checks that the *module names* declared in metadata match the names of the modules in the bytecode bundle [5](#0-4) . It does not verify that the declared source, manifest, or digest actually decompile/hash to the published bytecode.

As a result, a malicious publisher can publish arbitrary bytecode under a `PackageRegistry` entry whose `manifest`/`source`/`source_digest` metadata describes entirely different, benign-looking source code (e.g., matching an audited open-source repository). Because the metadata fields are self-consistent (the publisher controls both the on-chain metadata and, separately, whatever source tree an auditor is asked to compile locally), `VerifyPackage` can be made to output `"Successfully verified source of package"` even though the deployed module bytes do not correspond to the audited/expected source at all.

### Impact Explanation
This breaks the "verified bytes ↔ package metadata ↔ committed module bytes" invariant explicitly relied upon by developers, auditors, and integrators who use `aptos move verify-package` to confirm that a deployed package matches its claimed/audited source before interacting with it or depending on it (dependencies can be pulled in as `immutable`/`compatible` deps via `code::check_dependencies`). A downstream user trusting a false "verified" result could unknowingly interact with or build on top of malicious bytecode believing it matches audited source, enabling supply-chain-style attacks (e.g., hidden backdoors, hidden admin functions) that are undetectable through the tool that is specifically designed to catch this class of mismatch.

### Likelihood Explanation
No special privileges are required — any package publisher can craft mismatched metadata/bytecode from the start (the chain does not stop them), and any user of the `aptos` CLI's `verify-package` feature is affected without any indication that bytecode was never checked. This is a systemic, permissionless issue in a documented security-facing tool.

### Recommendation
Fix `VerifyPackage::execute` to always fetch on-chain bytecode (`with_bytecode = true`) and additionally verify it byte-for-byte (or via a proper deterministic hash) against a fresh local compilation of the same modules, in addition to (not instead of) the existing metadata-field comparison. The `verify` function should be extended to accept and compare `Vec<Vec<u8>>` bytecode, not just `PackageMetadata`.

### Proof of Concept
1. Publish a package `P` on-chain with metadata (`manifest`, `modules[].source`, `source_digest`) matching some benign, publicly-auditable source `S`, but with actual bytecode compiled from a different, malicious source `S'` that has been hand-crafted to preserve the same module names as `S` (the only thing checked on-chain by `validate_publish_request`).
2. An auditor/user runs `aptos move verify-package --account <addr>` pointing their local `move_options` at source `S` (matching the metadata).
3. Because `CachedPackageRegistry::create(..., false)` never fetches on-chain bytecode, and `CachedPackageMetadata::verify` only compares metadata fields (which were crafted to match `S`), the command prints `"Successfully verified source of package"`.
4. In reality, the deployed bytecode is `S'`, not `S` — the tool gave a false positive, and the user is misled into trusting malicious code.

### Citations

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

**File:** aptos-move/framework/src/built_package.rs (L630-646)
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
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1816-1841)
```rust
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
