## Finding: `aptos move verify-package` never checks on-chain bytecode, only self-consistent metadata fields

### Title
`verify-package` reports "verified" without ever comparing on-chain module bytecode to source - ([File: aptos-move/cli/src/commands.rs])

### Summary
The Aptos CLI's `verify-package` command is documented as checking "that on-chain bytecode matches a local source tree" [1](#0-0)  but its implementation only compares `PackageMetadata` struct fields (name, deps, modules metadata, manifest, upgrade policy, extension, source digest) between a locally-built package and the on-chain registry entry. It never downloads or compares the actual `.mv` bytecode stored on-chain against the locally compiled bytecode.

### Finding Description
`VerifyPackage::execute` builds the package locally, extracts its `PackageMetadata`, and fetches the on-chain registry with `CachedPackageRegistry::create(client, self.account, false)` — note the hard-coded `false` for `with_bytecode`: [2](#0-1) 

`CachedPackageRegistry::create` only populates its `bytecode: BTreeMap` when `with_bytecode` is `true`; with `false` no module bytecode is ever fetched from chain: [3](#0-2) 

`package.verify(&compiled_metadata)` then compares only `PackageMetadata` fields — `name`, `deps`, `modules` (which is `ModuleMetadata`: `name`, `source`, `source_map`, `extension` — not bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest`: [4](#0-3) 

Critically, `ModuleMetadata.source` and `source_map` are attacker-supplied, arbitrary gzipped blobs with no cryptographic binding to the actual bytecode module that gets stored via `code::publish_package`. The on-chain Move framework itself performs no check that `source`/`source_digest` correspond to the compiled bytecode being published — `code::publish_package` only performs compatibility, dependency, and name-registration checks on the module bytes, never validating that metadata `source` fields hash to the same bytecode: [5](#0-4) 

Because `verify-package` recomputes the same metadata locally from the same (attacker-provided) source and compares it against the on-chain metadata (which is exactly that same metadata, since nothing validates it against real bytecode), the comparison will always "succeed" as long as the publisher used consistent metadata generation — regardless of what bytecode was actually committed on-chain. The bytecode itself is simply never examined by the verifier.

### Impact Explanation
Any user, auditor, exchange, or dependent-package owner relying on `aptos move verify-package` to confirm that on-chain code matches an audited source tree can be misled: an attacker can publish benign-looking source/metadata alongside arbitrary (e.g., backdoored, rug-pull, or malicious) bytecode, and `verify-package` will still report `"Successfully verified source of package"`. This breaks the code-authenticity/verification invariant the tool is documented to provide, enabling unauthorized or unaudited bytecode to appear as verified — directly matching "Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes."

### Likelihood Explanation
High. No privileged access or race condition is required — any package publisher can trigger this by construction (publish arbitrary bytecode with metadata generated from an unrelated "cover" source). The CLI's `with_bytecode` flag is hard-coded to `false` in `VerifyPackage::execute`, unlike `DownloadPackage` which supports fetching bytecode — this is a straightforward, deterministic implementation gap, not dependent on any race or governance assumption.

### Recommendation
`VerifyPackage::execute` should fetch on-chain bytecode (`CachedPackageRegistry::create(client, self.account, true)`), compile the local package to bytecode, and compare the actual module bytes (e.g., via hash) in addition to (or instead of relying solely on) metadata field equality. `CachedPackageMetadata::verify` should be extended to accept and check bytecode bytes/hashes per module before reporting success.

### Proof of Concept
1. Compile and publish a package where `ModuleMetadata.source` contains innocuous source code `A`, but the actual bytecode published under that module name is compiled from different, malicious source `B` (framework-level publish has no check binding `source` to bytecode — see `code::publish_package`).
2. A reviewer runs `aptos move verify-package --account <addr>` against local source `A`.
3. `VerifyPackage::execute` builds `A` locally, extracts `ModuleMetadata` (same `source`/`source_digest` as what was embedded on-chain), fetches on-chain `PackageMetadata` with `with_bytecode=false` (`aptos-move/cli/src/commands.rs:2121`), and calls `package.verify(&compiled_metadata)`.
4. Since `verify()` only compares metadata struct fields (never touches bytecode), and the on-chain metadata's `source`/`source_digest` were crafted to match local source `A`, the command returns `Ok("Successfully verified source of package")` — despite the actually-running bytecode being `B`.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L12-12)
```markdown
For code review and reproducibility, [`verify-package`](#aptos-move-verify-package) checks that on-chain bytecode matches a local source tree.
```

**File:** aptos-move/cli/src/commands.rs (L2119-2137)
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L159-231)
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
