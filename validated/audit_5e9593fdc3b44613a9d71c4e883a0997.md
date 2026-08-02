## Title
`aptos move verify-package` reports success without ever comparing on-chain bytecode to recompiled bytecode - ([File: aptos-move/cli/src/stored_package.rs])

### Summary
The Aptos CLI's `verify-package` command is meant to give users/auditors an assurance that the bytecode published on-chain under a given address matches a given (recompiled) source. In practice, `StoredPackageMetadata::verify()` never compares actual module bytecode - only `PackageMetadata` fields (name, deps, module *metadata* records, manifest, upgrade policy, extension, source digest). The raw on-chain bytecode is neither fetched nor diffed against the locally-built bytecode, so a package can present benign, matching source/metadata while running arbitrary bytecode on-chain, and the tool will still print "Successfully verified source of package."

### Finding Description
`VerifyPackage::execute` builds the package locally, extracts its `PackageMetadata`, then fetches the on-chain registry **without bytecode**: [1](#0-0) 
```
let registry = CachedPackageRegistry::create(client, self.account, false).await?;
...
package.verify(&compiled_metadata)?;
Ok("Successfully verified source of package")
```
`CachedPackageRegistry::create` only downloads bytecode `if with_bytecode` is true; here it is passed `false`: [2](#0-1) 

`verify()` itself compares `name`, `deps`, `modules` (which is `ModuleMetadata` - `name`, zipped `source`, `source_map`, `extension`, **not the compiled bytes**), `manifest`, `upgrade_policy`, `extension`, and `source_digest`: [3](#0-2) 

Separately, `get_bytecode()` exists on `CachedPackageRegistry` and can fetch on-chain module bytes via `client.get_account_module`, but it is never invoked from `verify()` or from `VerifyPackage::execute`: [4](#0-3) 

On-chain, `code::publish_package` stores `PackageMetadata` (source text/manifest/digest) completely independently from the raw `code: vector<vector<u8>>` bundle that is actually loaded and executed by `request_publish`/`request_publish_with_allowed_deps`: [5](#0-4) 
There is no on-chain (or off-chain, via this tool) enforcement that the bytecode in `code` was actually compiled from the source recorded in `PackageMetadata.modules[i].source`. The bytecode verifier and compatibility checker validate structural/type-safety and upgrade-compatibility properties of whatever bytecode is submitted, but do not check that it was produced by compiling the accompanying source text. The `verify-package` CLI command is the tool users are pointed to for that specific "source matches deployed bytecode" guarantee, and it silently fails to provide it.

### Impact Explanation
This is a mismatch between "verified" bytes (source/metadata) and the actually committed module bytes, which is explicitly called out as a valid analog category ("Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes"). Any account can publish honest-looking source/metadata (matching name, manifest, dependencies, source digest) alongside maliciously crafted bytecode with a divergent implementation, and downstream consumers, auditors, or integrators using `aptos move verify-package` to confirm code authenticity before trusting/interacting with a deployed package would receive a false "verified" result. This is a supply-chain-trust bypass: it defeats the entire purpose of the verification feature and could enable social-engineering-adjacent but code-driven trust deception at scale for any published or upgraded package (compat or immutable policy) on mainnet.

### Likelihood Explanation
Likelihood is high for the vulnerability to be reachable and misleading, since every call to `verify-package` goes through the same `create(..., false)` path and `verify()` never touches bytecode. Exploitation requires no special privileges; any package publisher/upgrader can supply source metadata that does not match the actual bytecode blob, since these are independent transaction parameters. The main limiting factor is that this is a CLI/tooling correctness issue (not a direct consensus-state corruption), so its severity depends on how much users rely on this specific command versus recompiling and diffing bytecode themselves.

### Recommendation
- Change `VerifyPackage::execute` to call `CachedPackageRegistry::create(client, self.account, true)` to fetch on-chain bytecode.
- Extend `verify()` (or add a companion check) to compare each module's on-chain bytecode (via `get_bytecode`) against the bytecode produced by `BuiltPackage::extract_code()` for the locally recompiled package, and fail verification on any mismatch.
- Document clearly that until this is fixed, `verify-package` only checks source/metadata consistency and does **not** guarantee that deployed bytecode was compiled from the shown source.

### Proof of Concept
1. Build and publish a package `P` at address `A` where the submitted `code` bundle contains a maliciously modified module `m.mv` (e.g., function `withdraw` drains funds to attacker), while the `metadata_serialized.modules[0].source` field is set to the benign, publicly-reviewed source of `m.move` (unrelated to the actual bytecode). Both are independent parameters to `code::publish_package_txn`/`object_code_deployment::publish`, so nothing on-chain rejects this combination.
2. An auditor/user runs `aptos move verify-package --account A` against the legitimate `m.move` source tree.
3. `VerifyPackage::execute` builds `compiled_metadata` from the legitimate source, fetches `CachedPackageRegistry::create(client, A, false)` (bytecode not fetched), and calls `registry.get_package(...).verify(&compiled_metadata)`.
4. Because `name`, `deps`, `modules` (source text matches, by construction), `manifest`, `upgrade_policy`, `extension`, and `source_digest` all match, `verify()` returns `Ok(())`, and the CLI prints `"Successfully verified source of package"`, even though the actually deployed bytecode is the malicious module, not one compiled from the shown source.

### Citations

**File:** aptos-move/cli/src/commands.rs (L2119-2139)
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
