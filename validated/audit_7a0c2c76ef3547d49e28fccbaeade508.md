### Title
`aptos move verify-package` never compares actual on-chain module bytecode, only source-derived metadata - ([File: aptos-move/cli/src/stored_package.rs])

### Summary
The `VerifyPackage` CLI command claims to verify that "the bytecode matches a local compilation of the Move code," but the implementation only compares `PackageMetadata` fields (name, deps, `ModuleMetadata` source/source_map, manifest, upgrade policy, extension, source digest). It never fetches or diffs the actual compiled `.mv` bytecode bytes stored on-chain against the bytecode produced by the local build.

### Finding Description
`VerifyPackage::execute` explicitly disables bytecode retrieval when constructing the registry: [1](#0-0) 
`CachedPackageRegistry::create(client, self.account, false)` is called with `with_bytecode = false`, so `CachedPackageRegistry::bytecode` stays empty: [2](#0-1) 

The subsequent check is done exclusively via `CachedPackageMetadata::verify`, which compares `name`, `deps`, `modules` (i.e. `ModuleMetadata`, containing only `name`, gzipped `source`, and `source_map` — not compiled bytes), `manifest`, `upgrade_policy`, `extension`, and `source_digest`: [3](#0-2) 

`ModuleMetadata` itself carries no bytecode hash field at all — only the gzipped source text and source map: [4](#0-3) 

Because `source_digest` is computed from the package's source files (per the doc comment on `PackageMetadata`: "constructed by first building the sha256 of each individual source... than sorting them alphabetically, and sha256 them again"), matching `source_digest` and `modules` only proves the *claimed source text* embedded in metadata is self-consistent with the local recompiled source. It proves nothing about what module bytes were actually verified, compiled, and stored by the Move VM at publish time via `code::publish_package_txn` / `request_publish`, which is handled entirely independently in `aptos-move/framework/natives/src/code.rs` and `aptos_vm.rs`'s publish flow.

There is no cryptographic linkage enforced on-chain between the gzipped `source`/`manifest` fields in `PackageMetadata` and the actual `CompiledModule` bytes passed in the `code: vector<vector<u8>>` argument to `publish_package`/`publish_package_txn`. A publisher can submit arbitrary bytecode in `code` while embedding unrelated (or benign-looking) source text and a self-consistent `source_digest` in the metadata. Since the CLI's `verify-package` never downloads and diffs the real `.mv` bytes (it explicitly passes `with_bytecode: false`), a reviewer using `aptos move verify-package` to confirm "on-chain bytecode matches local source" will get a false "Successfully verified source of package" result even when the deployed bytecode differs arbitrarily from what the source claims.

### Impact Explanation
This breaks the code-safety invariant "verified bytes, package metadata, dependency declarations, and committed module bytes must agree" called out in the Publish Impact Gate. Anyone relying on `aptos move verify-package` (auditors, integrators, block explorers, downstream dependency consumers checking `compat`/`immutable` packages before depending on them) can be misled into believing deployed bytecode matches published source, when in fact the actual bytecode was never checked. This is a code-safety/verification bypass with real-world relevance since `verify-package` is the primary tool documented for "code review and reproducibility" (`third_party/move/documentation/book/src/cli-deploy.md` line 12) and is used to vet packages before establishing trust or dependency relationships.

### Likelihood Explanation
High likelihood of triggering under normal usage: any user who runs `aptos move verify-package` against a maliciously or accidentally mismatched package will get a false-positive "verified" result, because the code path always sets `with_bytecode = false` and the `verify` function has no bytecode-based check at all — this isn't an edge case requiring special conditions, it is the command's only and always-executed code path.

### Recommendation
In `VerifyPackage::execute` (`aptos-move/cli/src/commands.rs`), call `CachedPackageRegistry::create` with `with_bytecode: true`, fetch the on-chain bytecode for every module in the package, and compare it byte-for-byte (or by hash) against the bytecode produced by the local `BuiltPackage` build, in addition to (not instead of) the existing metadata/source_digest comparison in `CachedPackageMetadata::verify`.

### Proof of Concept
1. Build and publish a package where `code` (the compiled module bytes passed to `code::publish_package_txn`) is swapped for a different but ABI/compat-compatible module, while the `PackageMetadata.modules[].source` / `manifest` / `source_digest` fields are crafted (e.g., left matching the original, honest source) so that a local recompilation of that same source produces an identical `PackageMetadata`.
2. Run `aptos move verify-package --account <addr>` pointing at a local checkout of the "honest" source.
3. Observe that `VerifyPackage::execute` succeeds and prints "Successfully verified source of package" because:
   - `CachedPackageRegistry::create(client, account, false)` (`aptos-move/cli/src/commands.rs:2121`) never downloads the real `.mv` bytecode.
   - `package.verify(&compiled_metadata)` (`aptos-move/cli/src/stored_package.rs:193-241`) only compares metadata/source-derived fields, none of which reflect the actual bytecode that was staged, compatibility-checked, and stored by `StagingModuleStorage::create` in `third_party/move/move-vm/runtime/src/storage/publishing.rs`.
4. This demonstrates that "verified" bytecode via the CLI tool does not correspond to what is actually committed on-chain, satisfying the Publish Impact Gate criterion of a mismatch between verified bytes and committed module bytes.

Note: I was unable to fully inspect `built_package.rs`'s `source_digest`/`extract_metadata` computation logic (the file appeared largely empty in the index, likely due to index size limits truncating its content), so the exact byte-level construction of `source_digest` from source files could not be independently confirmed beyond the doc comment in `code.move`. If a full session is needed to trace this further or to patch the CLI, I'd recommend starting a Devin session with repository access.

### Citations

**File:** aptos-move/cli/src/commands.rs (L2118-2137)
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L55-60)
```text
    /// Metadata about a module in a package.
    struct ModuleMetadata has copy, drop, store {
        /// Name of the module.
        name: String,
        /// Source text, gzipped String. Empty if not provided.
        source: vector<u8>,
```
