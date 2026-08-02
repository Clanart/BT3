Confirmed: `VerifyPackage::execute` in `aptos-move/cli/src/commands.rs` creates the `CachedPackageRegistry` with `with_bytecode = false` and only calls `package.verify(&compiled_metadata)`, and `CachedPackageMetadata::verify` in `aptos-move/cli/src/stored_package.rs` only compares `PackageMetadata` struct fields (`name`, `deps`, `modules` metadata records, `manifest`, `upgrade_policy`, `extension`, `source_digest`) — it never fetches or diffs the actual on-chain compiled module bytecode via `get_account_module`/`get_bytecode`.

### Title
`aptos move verify-package` never compares on-chain module bytecode, only package metadata fields - (File: aptos-move/cli/src/stored_package.rs)

### Summary
The CLI's package-verification path is meant to let a user or auditor confirm that on-chain code matches an audited local source tree, per its own documentation ("checks that on-chain bytecode matches a local source tree" — `third_party/move/documentation/book/src/cli-deploy.md:12`). In the actual implementation, this check never inspects the real deployed bytecode.

### Finding Description
`VerifyPackage::execute` (`aptos-move/cli/src/commands.rs:2098-2140`) builds a `CachedPackageRegistry` via `CachedPackageRegistry::create(client, self.account, false)` — the third argument `with_bytecode` is `false` [1](#0-0) . Because `with_bytecode` is `false`, `CachedPackageRegistry::create` never populates its `bytecode: BTreeMap<String, Vec<u8>>` field with `get_account_module` results [2](#0-1) .

The verification itself, `CachedPackageMetadata::verify`, only compares the locally-built `PackageMetadata` against the on-chain `PackageMetadata` struct fields: `name`, `deps`, `modules` (which is `ModuleMetadata` — name/source/source_map/extension, i.e. declared metadata, not the compiled module bytes themselves), `manifest`, `upgrade_policy`, `extension`, and `source_digest` [3](#0-2) . At no point does `verify()` call `get_bytecode()` or otherwise obtain the actual on-chain `.mv` bytecode published under the account (via `0x1::code::PackageRegistry`'s associated module resources) to byte-compare it against the freshly compiled module from the local source tree.

`source_digest` (computed on-chain as the sha256 over each source file, sorted, then re-hashed — see `PackageMetadata.source_digest` docs in `code.move:36-38`) is a digest of *source text*, not of compiled bytecode. Because Move compilation is not guaranteed to be bit-for-bit reproducible across compiler versions/flags/toolchains, or because the on-chain module bytes could diverge from what the declared source would produce (e.g. `deploy-object`/`upgrade-object` flows, chunked publishing via `large_packages.move`, or any path that assembles code before calling `code::publish_package_txn`), a matching `source_digest` and matching metadata fields do not prove the deployed bytecode is what a reviewer expects.

### Impact Explanation
This is a code-safety/verification-integrity gap rather than a direct on-chain privilege bypass: `verify-package` gives users/auditors false assurance that "Successfully verified source of package" (the literal success message at `commands.rs:2139`) means the deployed bytecode matches audited source, when in fact only package metadata records were compared. Anyone relying on `aptos move verify-package` (e.g. before interacting with, depending on, or approving a dependency package) could be misled into trusting bytecode that was never actually diffed. This directly matches the required-impact class "Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes."

However, this is a CLI-side, off-chain informational/tooling gap — it does not itself let an attacker publish, upgrade, freeze, or take ownership of on-chain code, nor does it corrupt any protected on-chain state. It only degrades the trustworthiness of an auditing tool.

### Likelihood Explanation
Every invocation of `aptos move verify-package` follows exactly this code path unconditionally (`with_bytecode: false` is hardcoded, not a flag), so the gap is deterministic and always present, not a corner case. Any user who depends on this command as their "did the bytecode match" check is systematically affected.

### Recommendation
Have `CachedPackageRegistry::create` fetch the on-chain module bytecode (`with_bytecode = true`) in the `VerifyPackage` flow, and extend `CachedPackageMetadata::verify` to byte-compare each on-chain module's compiled bytecode against the freshly compiled local module bytes (not merely the `ModuleMetadata` records), rather than relying solely on `source_digest`/metadata equality.

### Proof of Concept
Not directly exploitable as an on-chain unauthorized-publish PoC — the issue only affects the local/off-chain `verify-package` command output, which is why this finding is reported as a code-safety/verification gap rather than a critical unauthorized-mutation vulnerability. Concretely: run `aptos move verify-package --account <addr>` against any on-chain package; observe in `commands.rs:2121-2137` that only `registry = CachedPackageRegistry::create(client, self.account, false)` and `package.verify(&compiled_metadata)` are invoked, with no call to `get_bytecode`/`get_account_module` for byte-level bytecode comparison, confirming the tool can report success without ever inspecting the deployed `.mv` bytes.

### Citations

**File:** aptos-move/cli/src/commands.rs (L2119-2126)
```rust
        // Now pull the compiled package
        let client = self.rest_options.client(&self.profile_options)?;
        let registry = CachedPackageRegistry::create(client, self.account, false).await?;
        let package = registry
            .get_package(pack.name())
            .await
            .map_err(|s| CliError::CommandArgumentError(s.to_string()))?;

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
