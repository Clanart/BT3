### Title
`aptos move verify-package` (`CachedPackageMetadata::verify`) never compares on-chain module bytecode to the locally rebuilt bytecode - (File: `aptos-move/cli/src/stored_package.rs`)

### Summary
The Aptos CLI's package-verification helper, `CachedPackageMetadata::verify()`, is documented and used to prove that on-chain code matches a local source tree. In reality it only diffs `PackageMetadata` fields (`name`, `deps`, `modules`, `manifest`, `upgrade_policy`, `extension`, `source_digest`); it never compares the actual compiled bytecode retrieved from chain against bytecode produced by a local rebuild. `ModuleMetadata` itself carries `source`/`source_map` (compressed source text), not a bytecode digest, so identical metadata can coexist with differing on-chain bytecode.

### Finding Description
`verify()` is defined at [1](#0-0)  and only checks:
- `name`, `deps`, `modules` (which is `Vec<ModuleMetadata>` containing `name`/`source`/`source_map`, not bytecode), `manifest`, `upgrade_policy`, `extension`, `source_digest`.

`CachedPackageRegistry::create` does separately fetch raw module bytecode into a `bytecode: BTreeMap<String, Vec<u8>>` via `get_account_module` when `with_bytecode` is true [2](#0-1) , and `get_bytecode()` exposes it [3](#0-2) , but the fetched bytecode is never fed into `verify()` — `verify()`'s signature only takes a `&PackageMetadata` and performs no bytecode comparison at all.

This matters because `verify-package` is documented as the tool that "checks that on-chain bytecode matches a local source tree" [4](#0-3) . Publishing itself (`code::publish_package_txn` / native `request_publish_with_allowed_deps`) only checks that bytecode module *names* correspond to the metadata's declared module names [5](#0-4)  and enforces compatibility/dependency/resource-group/event constraints — none of these validate that the submitted bytecode is the actual compilation output of the accompanying `ModuleMetadata.source`. Nothing on-chain or in the CLI cross-checks compiled bytecode bytes against the declared source or against a rebuild.

### Impact Explanation
A package publisher can submit metadata (name, source text, manifest, dependency list, upgrade policy) that looks benign and matches what a reviewer expects, while the actually-published module bytecode differs from what compiling that declared source would produce (e.g., different logic, backdoors, or stale/malicious bytecode reused from elsewhere). Downstream consumers, auditors, or governance processes that rely on `aptos move verify-package` to attest "on-chain code == this source" would get a false pass, because `verify()` never reads or compares bytecode. This directly matches the required impact class: "Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes," creating a code-safety/trust gap in the publish-verification tooling used to vet mainnet contracts.

### Likelihood Explanation
High feasibility: the mismatch is not a hypothetical edge case but a structural omission in `verify()` — the function simply has no code path that touches the fetched `bytecode` map. Any package author (unprivileged, no special access needed) can trigger the discrepancy by publishing metadata/source that doesn't match the deployed bytecode; the CLI's verification flow performs zero bytecode-level cross-check for either the initial publish path or upgrades. The vulnerability requires no race condition or privileged action — it is inherent in how `verify()` is written.

### Recommendation
Extend `CachedPackageMetadata::verify()` (or the CLI's `verify-package` command) to:
1. Recompile the local package to bytecode with the same build options used at publish time.
2. Fetch on-chain bytecode via `CachedPackageRegistry::create(..., with_bytecode = true)` / `get_bytecode()`.
3. Byte-for-byte compare the recompiled bytecode against the on-chain bytecode for every module in the package, failing verification on any mismatch, in addition to the existing metadata-field comparisons.

### Proof of Concept
1. Build and publish a package where the declared `ModuleMetadata.source` compiles to module `M` with benign logic, but supply hand-crafted bytecode for `M` (bytecode differs from what compiling the declared source produces) in the `code: vector<vector<u8>>` argument to `code::publish_package_txn`. The bytecode only needs to keep the same module name (`self_id().name()`), satisfy the compatibility check (`compatibility.check`), and pass the standard verifier/module-init checks — none of which compare it to `ModuleMetadata.source`.
2. Run `aptos move verify-package --account <addr>` against the local (honest) source tree.
3. Observe that `CachedPackageMetadata::verify()` at [1](#0-0)  returns `Ok(())` because all compared fields (name/deps/modules/manifest/upgrade_policy/extension/source_digest) match, even though the actual deployed bytecode is not the compiled output of that source.

Note: I was not able to fully trace whether the `VerifyPackage` CLI command in `aptos-move/cli/src/commands.rs` performs any additional bytecode diff outside of calling `CachedPackageMetadata::verify()` (6 references were found there but not read in full due to iteration limits); if such a check exists elsewhere in that command it would mitigate this finding, so this should be confirmed against `aptos-move/cli/src/commands.rs`'s `VerifyPackage`/`verify-package` implementation before treating this as conclusively unmitigated.

### Citations

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

**File:** aptos-move/cli/src/stored_package.rs (L108-117)
```rust
    /// Gets the bytecode associated with the module.
    pub async fn get_bytecode(
        &self,
        module_name: impl AsRef<str>,
    ) -> anyhow::Result<Option<&[u8]>> {
        Ok(self
            .bytecode
            .get(module_name.as_ref())
            .map(|v| v.as_slice()))
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

**File:** third_party/move/documentation/book/src/cli-deploy.md (L70-76)
```markdown
## `aptos move verify-package`

Build the package locally and verify that the on-chain copy matches.

```shellscript filename="Terminal"
aptos move verify-package --account 0xABC...123
```
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1818-1824)
```rust
        for m in modules {
            if !expected_modules.remove(m.self_id().name().as_str()) {
                return Err(Self::metadata_validation_error(&format!(
                    "unregistered module: '{}'",
                    m.self_id().name()
                )));
            }
```
