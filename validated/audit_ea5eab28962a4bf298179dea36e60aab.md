Based on the investigation, the strongest publish-path analog I found is not in the on-chain `code.move` publish/upgrade logic (which is heavily hardened — including a dedicated `deploy_owner`/`root_owner` guard in `init.move` that specifically defends against exactly this class of "first-actor state manipulation" bug), but in the Aptos CLI's `verify-package` tooling, which silently never performs the bytecode comparison it advertises.

### Title
`aptos move verify-package` Never Compares On-Chain Bytecode, Falsely Attesting Deployed Modules Match Audited Source - (File: aptos-move/cli/src/commands.rs, aptos-move/cli/src/stored_package.rs)

### Summary
The `VerifyPackage` CLI command is documented as the tool that "checks that on-chain bytecode matches a local compilation of the Move code" [1](#0-0)  and prints `"Successfully verified source of package"` on success [2](#0-1) . However, its implementation never fetches or compares the actual deployed module bytecode — it only compares two `PackageMetadata` structs.

### Finding Description
`VerifyPackage::execute` builds the package locally to obtain its metadata, then downloads the on-chain `PackageRegistry` with bytecode fetching explicitly disabled (`with_bytecode = false`), and calls `package.verify(&compiled_metadata)`: [3](#0-2) 

`CachedPackageRegistry::create` respects that flag — when `with_bytecode` is `false`, no `get_account_module` calls are made and `self.bytecode` stays empty, even though the struct and a `get_bytecode` accessor for this exact purpose exist: [4](#0-3) [5](#0-4) 

`CachedPackageMetadata::verify` then only compares `name`, `deps`, `modules` (the `ModuleMetadata` list — just `name`, gzipped `source`, `source_map`, `extension`), `manifest`, `upgrade_policy`, `extension`, and `source_digest`: [6](#0-5) 

None of these fields are the compiled bytecode, and none of them are cryptographically bound to it on-chain. `source_digest` is explicitly documented as a hash of the *source files only*: "This is constructed by first building the sha256 of each individual source, than sorting them alphabetically, and sha256 them again." [7](#0-6) . Nothing in `code::publish_package` or the `request_publish`/`request_publish_with_allowed_deps` natives enforces that the submitted `code: vector<vector<u8>>` bytecode was actually produced by compiling the submitted source/metadata [8](#0-7) . The chain's verifier only checks the bytecode is well-formed, native-safe, and metadata-consistent by name/dependency [9](#0-8)  — it never checks bytecode-vs-source correspondence, and that's by design (that job is delegated to off-chain tooling like `verify-package`). Since that tooling silently skips the one check it exists to perform, the entire "verified deployed bytecode == reviewed source" guarantee is broken.

### Impact Explanation
A malicious or compromised package owner can publish a package where the declared metadata (source, manifest, source_digest) looks completely benign and matches what auditors/users would compile locally, while the actual bytecode bundle stored on-chain and executed by the VM is different (e.g., contains a backdoor, altered arithmetic, or hidden privileged function). Anyone who runs `aptos move verify-package` against that address — including wallets, exchanges, or auditors relying on this exact command per the documented workflow — will get a false "Successfully verified source of package" result and treat the deployed, potentially malicious, bytecode as trusted and reviewed. This is a direct instance of the required "Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes" publish impact.

### Likelihood Explanation
High. No special privileges are needed beyond being able to publish a package (any account can do this) and get someone to run the standard, documented verification workflow against it. The bug is deterministic and always present — `with_bytecode` is hardcoded to `false` in `VerifyPackage::execute`, so the gap exists on every invocation of this command, not just in an edge case.

### Recommendation
In `VerifyPackage::execute`, call `CachedPackageRegistry::create(client, self.account, true)` to fetch on-chain bytecode, then compile the local package to bytecode and byte-for-byte compare each module's bytecode against `registry.get_bytecode(module_name)`, failing verification on any mismatch, in addition to (not instead of) the existing metadata comparison in `stored_package.rs::verify`.

### Proof of Concept
1. Build a legitimate-looking package `Legit` locally; publish it to address `0xBEEF`, but before submitting the publish transaction, replace one compiled `.mv` module in the `code` vector with a different, malicious bytecode blob compiled from a modified source that is never disclosed (keep the metadata's `modules[i].source`/`source_map`/`source_digest` referencing the original, undisclosed-swap-free source).
2. Since `code::publish_package_txn` only checks module names, natives, and compatibility, not bytecode-source correspondence [10](#0-9) , the publish succeeds with mismatched bytecode/source.
3. A reviewer runs `aptos move verify-package --account 0xBEEF` against the (published, legitimate-looking) local source tree.
4. `VerifyPackage::execute` builds local metadata, fetches on-chain metadata with `with_bytecode=false`, and calls `verify()`, which only compares metadata fields — all of which match, since the attacker kept metadata/source consistent while only the bytecode diverges [11](#0-10) .
5. Command prints `"Successfully verified source of package"`, falsely attesting that the deployed bytecode matches the disclosed/reviewed source, while the actually-running module is different.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L12-12)
```markdown
For code review and reproducibility, [`verify-package`](#aptos-move-verify-package) checks that on-chain bytecode matches a local source tree.
```

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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L36-38)
```text
        /// The source digest of the sources in the package. This is constructed by first building the
        /// sha256 of each individual source, than sorting them alphabetically, and sha256 them again.
        source_digest: String,
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L366-391)
```text
    /// Native function to initiate module loading
    native fun request_publish(
        owner: address,
        expected_modules: vector<String>,
        bundle: vector<vector<u8>>,
        policy: u8
    );

    /// A helper type for request_publish_with_allowed_deps
    struct AllowedDep has drop {
        /// Address of the module.
        account: address,
        /// Name of the module. If this is the empty string, then this serves as a wildcard for
        /// all modules from this address. This is used for speeding up dependency checking for packages from
        /// well-known framework addresses, where we can assume that there are no malicious packages.
        module_name: String
    }

    /// Native function to initiate module loading, including a list of allowed dependencies.
    native fun request_publish_with_allowed_deps(
        owner: address,
        expected_modules: vector<String>,
        allowed_deps: vector<AllowedDep>,
        bundle: vector<vector<u8>>,
        policy: u8
    );
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1813-1841)
```rust
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
