## Finding: `aptos move verify-package` never checks on-chain bytecode, only self-reported metadata [1](#0-0) 

### Title
`VerifyPackage` CLI command claims to verify on-chain bytecode but only compares attacker-controlled metadata, never fetching or hashing the actual module bytecode - (File: `aptos-move/cli/src/commands.rs`, `aptos-move/cli/src/stored_package.rs`)

### Summary
The Aptos publish-verification analog to the "unused parameter" bug-class is a mismatch between what is *claimed* to be verified and what is *actually* verified. `aptos move verify-package` is documented to "check that on-chain bytecode matches a local source tree," but its implementation compares only `PackageMetadata` fields (name, deps, module source text/source map, manifest, upgrade policy, extension, source digest) and never downloads or hashes the actual on-chain module bytecode.

### Finding Description
`VerifyPackage::execute` builds the package locally to obtain `compiled_metadata`, then fetches the on-chain registry with bytecode fetching explicitly disabled: [2](#0-1) 

`CachedPackageRegistry::create(client, self.account, false)` passes `with_bytecode = false`, so the `bytecode: BTreeMap<String, Vec<u8>>` field of `CachedPackageRegistry` stays empty: [3](#0-2) 

The subsequent call `package.verify(&compiled_metadata)` only performs field-by-field equality checks on `PackageMetadata` (`name`, `deps`, `modules`, `manifest`, `upgrade_policy`, `extension`, `source_digest`): [4](#0-3) 

None of these fields are the actual compiled module bytes. `ModuleMetadata.source` and `source_map` are arbitrary, publisher-supplied gzip blobs stored in `code::PackageRegistry` on-chain (see `aptos-move/framework/aptos-framework/sources/code.move:56-65`), and `code::publish_package` never checks that this metadata's `source`/`source_digest` actually correspond to the bytecode being published — the only bytecode-related cross-check performed on-chain is that module *names* in the bundle match the module *names* listed in metadata (`validate_publish_request` in `aptos_vm.rs:1804-1865`), not that source content matches the bytecode.

Consequently, a malicious publisher can:
1. Publish arbitrary, malicious bytecode for a module.
2. Set the on-chain `PackageMetadata.modules[].source`/`source_map`/`source_digest`/`manifest` to whatever gzip blob and digest string they want — including one that is byte-identical to a legitimate, audited source tree (since these fields are just unconstrained `vector<u8>`/`String` values chosen by the publisher, not derived on-chain from the code).
3. Any third party (auditor, marketplace, wallet, block explorer) running `aptos move verify-package` against a legitimate local copy of that source tree will get "Successfully verified source of package," because `verify()` only compares metadata strings — it never inspects the real module bytecode at all.

### Impact Explanation
This breaks the code-safety invariant "verified bytes, package metadata, dependency declarations, and committed module bytes must agree." The tool is the canonical mechanism referenced by documentation for confirming on-chain code matches source (`third_party/move/documentation/book/src/cli-deploy.md:12,70-83`: "checks that on-chain bytecode matches a local source tree"). Its silent failure to fetch/compare bytecode means any downstream party who relies on `verify-package` output to establish trust in a deployed contract's behavior (e.g. before granting a dependency's `arbitrary`/`compat` allowance, before integrating with a DeFi protocol, before treating a package as "audited") is presented with a false verification success while the actual executing bytecode can be arbitrary and malicious. This is a high-impact code-safety/trust bypass in a permissionless publish flow, without needing any privileged access.

### Likelihood Explanation
High likelihood: no attacker privilege beyond normal permissionless publish rights is required, and constructing metadata whose `source`/`source_digest`/`manifest` fields match a legitimate local build is straightforward (the publisher fully controls these values when submitting the publish transaction; they are never independently derived from the bytecode by validation logic). Anyone auditing packages via the officially documented `verify-package` workflow is exposed.

### Recommendation
Fix `VerifyPackage::execute` (and `CachedPackageRegistry`) to always fetch on-chain bytecode (`with_bytecode = true`) and compare the SHA3/bytecode hash of each locally compiled module against the fetched on-chain module bytecode, in addition to (or instead of relying solely on) the metadata-string comparisons in `CachedPackageMetadata::verify`. Metadata-only equality should never be treated as sufficient proof that the on-chain bytecode matches the reviewed source.

### Proof of Concept
1. Publish package `P` at address `A` where the actual bytecode implements a malicious backdoor, but craft `PackageMetadata.modules[0].source` (gzip) and `source_digest` so they are byte-identical to a legitimate, benign source tree `S` (fully controllable by the publisher; `code::publish_package` does not verify this correspondence: `aptos-move/framework/aptos-framework/sources/code.move:159-231`).
2. Have a third party run `aptos move verify-package --account A` against a local checkout of `S`.
3. `VerifyPackage::execute` (`aptos-move/cli/src/commands.rs:2104-2140`) fetches metadata only (`with_bytecode=false`), builds `S` locally, and calls `package.verify(&compiled_metadata)` (`aptos-move/cli/src/stored_package.rs:193-241`), which compares only metadata fields — all of which were crafted to match.
4. Output: `"Successfully verified source of package"`, even though the deployed bytecode at `A` is malicious and unrelated to `S`.

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
