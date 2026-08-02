## Finding

Confirmed local root cause: `aptos move verify-package` (`VerifyPackage::execute`, calling `CachedPackageMetadata::verify`) does **not** verify actual on-chain module bytecode against the local compilation — it only compares `PackageMetadata` fields.

### Title
`aptos move verify-package` never compares on-chain module bytecode, only metadata — verification can pass while deployed bytecode differs from source - ([File: aptos-move/cli/src/stored_package.rs])

### Summary
`VerifyPackage::execute` builds the package locally, fetches the on-chain `PackageRegistry` **without bytecode** (`CachedPackageRegistry::create(client, self.account, false)`), and calls `package.verify(&compiled_metadata)`, printing `"Successfully verified source of package"` on success. [1](#0-0) 

### Finding Description
`CachedPackageMetadata::verify` only checks that `name`, `deps`, `modules` (the `ModuleMetadata` list — names/source/source_map/extension, **not bytecode**), `manifest`, `upgrade_policy`, `extension`, and `source_digest` fields match between the on-chain `PackageMetadata` and the locally rebuilt one. It never fetches or compares the raw `.mv` bytecode bytes actually stored under the account. [2](#0-1) 

`CachedPackageRegistry::create` only downloads bytecode when `with_bytecode` is `true`; `VerifyPackage::execute` passes `false`. [3](#0-2) [4](#0-3) 

Compare with `DownloadPackage`, where bytecode is only fetched/saved when `--bytecode` is explicitly passed. The tool's documentation claims `verify-package` "checks that on-chain bytecode matches a local source tree," but the implementation never performs this byte-for-byte check. [5](#0-4) 

The `source_digest` field is developer-supplied metadata (part of `PackageMetadata`, embedded at build time), not derived from a chain-side re-verification of the compiled bytecode against source; it is compared as an opaque string, so it only detects accidental mismatches from the same toolchain/inputs, not adversarial substitution of the actual bytecode with something that still contains a matching `source_digest`/module-name list (e.g., forged/copied metadata fields with different bytecode payload, or a build performed with a different compiler flag/target that changes emitted bytecode without changing the digest field checked here).

### Impact Explanation
This breaks the "verified bytes must match committed module bytes" invariant that downstream consumers rely on: anyone using `aptos move verify-package` (auditors, dependents, exchanges, marketplaces vetting a package before depending on it or granting it privileges) can be told "Successfully verified source of package" while the actual deployed bytecode is not proven to be a faithful compilation of the shown source. This is a code-safety/verification-integrity gap in the publish tooling rather than an on-chain state-mutation bug — it does not let an attacker publish or upgrade code without authorization (the underlying `code::publish_package` / `object_code_deployment` paths still enforce owner and compatibility checks correctly, as confirmed by the surrounding audit of `code.move`, `object_code_deployment.move`, and `aptos_vm.rs::validate_publish_request`). The severity is bounded because it is a client-side/CLI trust-verification tool, not a validator/VM-side control, and it doesn't affect consensus-critical state.

### Likelihood Explanation
Any user relying on `verify-package` for security assurance before trusting a deployed package will hit this gap; it requires no special access — just running the standard command, which prints a successful verification message without ever inspecting the bytecode by default.

### Recommendation
Make `VerifyPackage::execute` always fetch on-chain bytecode (`CachedPackageRegistry::create(..., true)`) and extend `CachedPackageMetadata::verify` (or a new check) to compare each module's downloaded on-chain bytes byte-for-byte against the freshly compiled bytecode from `pack.extract_code()`, failing verification on any mismatch. Update the documentation to accurately reflect current behavior until fixed.

### Proof of Concept
1. Publish a package `P` under account `A` where the on-chain `PackageMetadata` (name, deps, modules list, manifest, upgrade_policy, extension, source_digest) matches what a legitimate build of shown/expected source would produce, but the actual on-chain module bytecode (compiled with different logic, e.g., an injected backdoor function preserved under compatibility rules, or built with an altered compiler pass) differs from what compiling the "source" would produce.
2. Run `aptos move verify-package --account A` against the local source tree.
3. Observe `"Successfully verified source of package"` is printed because `verify()` never reads or compares `registry.get_bytecode(...)` output — only the metadata struct fields are checked.

Given that the deep-dive found the core on-chain publish invariants (ownership checks in `code::freeze_code_object`/`object_code_deployment::upgrade`, `expected_modules`/`allowed_deps` enforcement in `aptos_vm.rs::validate_publish_request`, and upgrade-policy/compatibility checks in `code.move`) to be properly enforced, this bytecode-vs-metadata verification gap in the CLI tooling is the strongest analog found; it is a Medium/lower-High severity tooling-integrity issue rather than a Critical unauthorized-publish vulnerability.

### Citations

**File:** aptos-move/cli/src/commands.rs (L2119-2140)
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

**File:** third_party/move/documentation/book/src/cli-deploy.md (L70-76)
```markdown
## `aptos move verify-package`

Build the package locally and verify that the on-chain copy matches.

```shellscript filename="Terminal"
aptos move verify-package --account 0xABC...123
```
```
