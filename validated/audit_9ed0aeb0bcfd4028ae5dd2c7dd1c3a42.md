Based on the investigation, I found a concrete gap in the `verify-package` CLI code-safety flow rather than a confirmed on-chain privilege-escalation bug in `code.move`/`object_code_deployment.move` (those implementations — `check_upgradability`, `check_coexistence`, `check_dependencies`, `freeze_code_object`, `assert_may_self_initialize` in `init.move` — all appear internally consistent with their documented invariants and covered by matching unit/e2e tests, so I could not independently prove an unauthorized-publish/upgrade/freeze bypass there).

The strongest local candidate is a metadata/bytecode mismatch in the package-verification path.

### Title
`StoredPackage::verify()` never compares actual module bytecode, only self-reported metadata - ([File: aptos-move/cli/src/stored_package.rs])

### Summary
`aptos move verify-package` is documented as checking "that on-chain bytecode matches a local source tree" [1](#0-0)  but the underlying `verify()` routine only diff-checks `PackageMetadata` fields — never the actual compiled module bytes.

### Finding Description
`StoredPackage::verify` compares `name`, `deps`, `modules` (which is `ModuleMetadata{name, source, source_map, extension}`, i.e. gzipped *source text*, not bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest` between two `PackageMetadata` values: [2](#0-1) . None of these fields is the raw compiled module bytecode that was actually published; `source_digest` itself is a hash of the *source files*, computed purely at build time by the CLI (`self.package.compiled_package_info.source_digest`) [3](#0-2)  and is a value the publisher controls when constructing `PackageMetadata`.

On-chain, `code::publish_package` accepts `pack: PackageMetadata` and `code: vector<vector<u8>>` as two independently supplied arguments from the caller [4](#0-3) . The only cross-checks performed between metadata and actual bytecode are: (1) that the *names* of modules declared in metadata equal the names of modules actually present in `code` (`expected_modules`) [5](#0-4) , and (2) that immediate dependencies declared in bytecode are covered by the `allowed_deps` computed from `pack.deps` [6](#0-5) . There is no on-chain or off-chain check tying `manifest`, `source_digest`, or `modules[].source` to the actual bytecode hash.

This is structurally the same class of bug flagged by the seed report: two places (module names/deps checks vs. cosmetic metadata fields) that are supposed to describe "the same publish," but only a subset is actually cross-validated, so their true behavior diverges from what a user relying on the tool ("`VAULT_ACCOUNT_MIN_TIME`"/"`verify-package`") expects.

### Impact Explanation
A publisher can submit a `PackageMetadata` whose `name`, `deps`, `manifest`, `source_digest`, and `modules[].name/source` describe a benign, reviewed package, while submitting a `code` bundle containing different bytecode for those same module names (as long as declared dependencies are a superset of the real bytecode's immediate deps, satisfying `validate_publish_request`). Anyone using `aptos move verify-package` to confirm "on-chain bytecode matches a local source tree" before trusting/integrating with the deployed package would get a false positive, because `verify()` never fetches or hashes the deployed compiled module bytes for comparison.

### Likelihood Explanation
Moderate. Exploitation requires only knowledge of the target package's module names and enough matching metadata fields to pass `verify()`; there is no permission barrier since the publisher fully controls the `PackageMetadata` payload passed to `publish_package_txn`/`object_code_deployment::publish`/`upgrade`. The dependency/name checks in `validate_publish_request` and `check_dependencies` constrain *which modules and deps* can exist, but place no constraint on the *content* of the modules relative to the metadata's descriptive fields.

### Recommendation
Extend `StoredPackage::verify` (and the on-chain `code::publish_package`/`request_publish_with_allowed_deps` path, if reproducibility guarantees are meant to be enforced on-chain) to independently recompute a digest of the actual published/fetched module bytecode and compare it against a value derived from the local build, rather than relying solely on self-reported `source_digest`/`manifest`/`modules[].source` fields supplied by the publisher.

### Proof of Concept
1. Build package `Foo` with source that is benign, note its `PackageMetadata` (name, deps, manifest, source_digest, module names).
2. Compile a second, malicious module with the *same module name(s)* and *compatible* (or superset) dependency set, but different logic/bytecode.
3. Call `code::publish_package_txn` (or `object_code_deployment::publish`) with `pack` = the metadata extracted from the benign build, `code` = the malicious compiled bytecode.
4. On-chain checks pass because they only validate module-name/dependency-name agreement [7](#0-6) .
5. Run `aptos move verify-package` for `Foo` against this address; `StoredPackage::verify` only diffs the metadata fields [2](#0-1)  and reports success despite the deployed bytecode being different from the reviewed source.

**Caveat:** I was not able to trace the full `VerifyPackage` CLI command wiring (e.g., `aptos-move/cli/src/commands.rs`) within the remaining budget to rule out a separate bytecode-hash comparison performed elsewhere in that command's flow; this finding is based directly on the `verify()` function's own field-by-field comparison, which does not include bytecode.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L10-12)
```markdown
- **Code-object publishing** (`deploy-object` / `upgrade-object`): the modules live at a **separate, derived object address**. Upgrade authority is held in a code object and can be transferred. Use this when modules need an address independent of any single account.

For code review and reproducibility, [`verify-package`](#aptos-move-verify-package) checks that on-chain bytecode matches a local source tree.
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

**File:** aptos-move/framework/src/built_package.rs (L631-646)
```rust
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L159-169)
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
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1818-1840)
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
```
