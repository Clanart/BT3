## Title
`aptos move verify-package` never fetches or compares on-chain bytecode, so it cannot detect a deployed module whose bytecode diverges from its self-reported package metadata/source - (File: aptos-move/cli/src/stored_package.rs)

### Summary
`aptos move verify-package` is documented as downloading a package and verifying that "the bytecode matches a local compilation of the Move code," but the implementation only compares self-reported `PackageMetadata` fields (name, deps, module names, manifest, upgrade policy, extension, source digest) between a local rebuild and the on-chain `PackageRegistry` resource. It never fetches the actually-deployed module bytecode to compare it byte-for-byte against the local compilation, even though the code has a `with_bytecode` flag specifically for that purpose which is left `false` at this call site.

### Finding Description
`CachedPackageRegistry::create` accepts a `with_bytecode: bool` flag. When `false`, it only loads the `PackageRegistry` resource (self-reported metadata) and skips downloading the actual `.mv` module bytecode: [1](#0-0) 

`VerifyPackage::execute` calls this with `with_bytecode = false`, then only calls `package.verify(&compiled_metadata)`: [2](#0-1) 

`CachedPackageMetadata::verify` only compares `PackageMetadata` struct fields — name, deps, `modules` (which itself is only `ModuleMetadata` — name, gzipped source, source map, no bytecode hash), manifest, upgrade policy, extension, and `source_digest` — never the actual bytecode bytes: [3](#0-2) 

The root problem is that `PackageMetadata` (including `source_digest`, computed purely from source-file hashes) is entirely self-reported by the publisher in the same transaction as the code bundle, and the Aptos VM's publish validation (`validate_publish_request`) never checks that the metadata's declared sources actually correspond to the submitted module bytecode — it only checks module *names* against `expected_modules`: [4](#0-3) 

Because nothing on-chain cryptographically binds `PackageMetadata.modules[i].source`/`source_digest` to the actual deployed bytecode, a malicious or compromised publisher can publish arbitrary/malicious bytecode alongside benign-looking metadata claiming innocuous source code. Anyone running `aptos move verify-package` against that account, expecting it to prove "the deployed code matches what I'm looking at," will get a false "Successfully verified source of package" result, because the tool merely confirms that recompiling the (attacker-controlled, self-reported) metadata locally reproduces the same self-reported metadata on chain — it never asks "does the running bytecode match this claimed source?" The `bytecode` field in `CachedPackageRegistry` exists precisely to close this gap but is unused by this command.

### Impact Explanation
This breaks the "mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes" invariant central to code-safety on Aptos. Users, auditors, wallets, or infra tooling that rely on `aptos move verify-package` to attest that an on-chain contract's behavior matches its published source are given a false sense of security: the command can report success for a package whose live bytecode is entirely different (and malicious) from what the source implies, since bytecode is never inspected. This can mask unauthorized code that diverges from what governance/users believe was reviewed or audited, directly enabling silent malicious code deployment/verification bypass on mainnet.

### Likelihood Explanation
High likelihood of triggering in practice: this is the default, only code path for `aptos move verify-package` (`with_bytecode` is hardcoded `false` there), requiring no special privilege beyond normal package publishing rights (which the attacker/publisher already has by definition). No race condition or governance bypass is needed — it is a straightforward, deterministic gap in the verification logic that always applies whenever this CLI command is used against an adversarial publisher.

### Recommendation
Make `VerifyPackage::execute` call `CachedPackageRegistry::create(client, self.account, true)` to download the actual on-chain bytecode for each module, then compare it (e.g., via `pack.extract_code()` compiled bytes) byte-for-byte (or via `sha256`) against the locally-built bytecode for each module name, in addition to the existing metadata comparison in `CachedPackageMetadata::verify`. Treat any bytecode mismatch as a hard verification failure.

### Proof of Concept
Conceptual PoC (cannot be executed without a live network/CLI environment, but the code path is directly readable):
1. Attacker authors module `M` with malicious logic, compiles it, and crafts `PackageMetadata` whose `modules[0].source` (gzipped) instead contains a benign-looking source string, and whose `source_digest` is computed to be internally self-consistent with that fake source (this is trivial because `source_digest` is computed client-side over the source blob the attacker controls, not the bytecode).
2. Attacker calls `code::publish_package_txn` with the malicious bytecode `code: vector<vector<u8>>` and the crafted, self-consistent-but-false `metadata_serialized`. `validate_publish_request` only checks that `M`'s name is in `expected_modules` (derived from the same crafted metadata) — it never decompiles/verifies that `code[0]` corresponds to `modules[0].source`, so the transaction succeeds: [5](#0-4) 
3. A third party, given the (fake) benign source, locally builds the same fake source into `compiled_metadata`, then runs `aptos move verify-package --account <attacker>`. `VerifyPackage::execute` fetches `PackageRegistry` (no bytecode) and calls `package.verify(&compiled_metadata)`, which succeeds because both the local rebuild and on-chain metadata were derived from the same fake source: [6](#0-5) 
4. The tool prints "Successfully verified source of package," even though the bytecode actually executing at `attacker`'s address is the malicious module, not a compilation of the "verified" source — because bytecode bytes were never fetched or compared.

### Citations

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
