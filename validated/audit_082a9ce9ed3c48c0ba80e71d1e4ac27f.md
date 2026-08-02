### Title
`aptos move verify-package` never compares on-chain bytecode, only self-reported metadata - (File: `aptos-move/cli/src/stored_package.rs`)

### Summary
The `aptos move verify-package` CLI command is documented as verifying "that the on-chain copy matches" a local build [1](#0-0) , but its implementation never fetches or diffs the actual on-chain compiled bytecode. It only compares self-reported `PackageMetadata` fields, none of which are cryptographically bound to the bytecode array that `code::publish_package` actually stores and executes.

### Finding Description
`VerifyPackage::execute` builds the package locally, then loads the on-chain registry with `CachedPackageRegistry::create(client, self.account, false)` — the `false` disables bytecode download entirely [2](#0-1) . It then calls `package.verify(&compiled_metadata)`, whose full comparison is: package name, `deps`, `modules` (name/source/source_map/extension), `manifest`, `upgrade_policy`, `extension`, and `source_digest` [3](#0-2) . The raw `.mv` bytecode is never fetched or compared in this path.

`source_digest` itself is computed purely from local source `.move` files and `Move.toml`, hashed independently of the compiled bytecode [4](#0-3) , and `ModuleMetadata.source`/`source_map` are attacker-suppliable gzip blobs attached at publish time [5](#0-4) .

Critically, `code::publish_package` (Move framework) accepts `pack: PackageMetadata` and `code: vector<vector<u8>>` as two independent parameters and never asserts that `pack.modules[i].source`, when compiled, produces the bytes in `code[i]` [6](#0-5) . The native `request_publish`/`request_publish_with_allowed_deps` calls only validate module names/addresses/dependency policy against `bundle`, not against the metadata's `source` field [7](#0-6) .

The result: a publisher can submit a `PackageMetadata` whose `modules[i].source` (and matching `source_digest`) describes benign, auditable code, while the actual `code` bundle stored and executed on-chain contains different (malicious) bytecode. `aptos move verify-package`, run by a downstream integrator/auditor to confirm the deployed module matches the published source before trusting it, will report "Successfully verified source of package" even though it never looked at the executing bytecode at all.

### Impact Explanation
This breaks the code-safety invariant that "package metadata... must describe the code that is actually verified and stored." Any protocol, wallet, or integrator that relies on `aptos move verify-package` as an attestation that a deployed contract's logic matches its published/audited source can be misled into trusting malicious bytecode — enabling supply-chain-style publish/upgrade trust bypass without needing any on-chain privilege escalation. Because verification tooling is the sole mechanism suggested by the docs for "code review and reproducibility" [8](#0-7) , this is a high-impact trust/verification bypass at mainnet relevance.

### Likelihood Explanation
High likelihood: any account (unprivileged, permissionless publish flow) can construct a `publish_package_txn`/chunked-publish transaction with metadata whose `source`/`source_digest` fields diverge from the actual `code` bundle — there is no on-chain or off-chain enforcement linking them. The `CachedPackageRegistry::create(..., false)` call in `VerifyPackage` deterministically skips bytecode fetch/comparison for every invocation of `verify-package`, so the gap is unconditional, not merely a timing race.

### Recommendation
- Make `aptos move verify-package` always fetch on-chain bytecode (`with_bytecode = true`) and byte-compare it against the locally-recompiled module bytecode, in addition to (or instead of) comparing metadata fields.
- Consider having `code::publish_package` (or the native publish handler) reject or flag packages where `ModuleMetadata.source`, when independently recompiled, does not produce the accompanying `code` bytes, or at minimum document clearly that `source`/`source_digest` are unauthenticated, advisory-only fields not suitable for establishing code-safety guarantees.
- Update the CLI documentation to accurately describe that `verify-package` currently only checks metadata equivalence, not executable bytecode equivalence, until the bytecode comparison is implemented.

### Proof of Concept
1. Compile a benign Move module `A` and build its package metadata (`PackageMetadata` with `modules[0].source` = benign source, `source_digest` matching that source).
2. Before publishing, swap the `code` vector passed to `code::publish_package_txn` (or `object_code_deployment::publish`) for a different, malicious compiled module `B` whose module name/address are made to match `A`'s (module names/addr only need to satisfy the sender-address and duplicate-name checks in `code.move`/`publishing.rs`; nothing checks `B` against `A`'s `source`).
3. Submit the transaction; `code::publish_package` succeeds because it never diffs `pack.modules[i].source` against `code[i]`.
4. A third party runs `aptos move verify-package --account <addr>` using the benign local source for `A`. Because `CachedPackageRegistry::create(..., false)` skips bytecode download, and `verify()` only compares metadata fields (which match, since the metadata was crafted from `A`'s source), the command prints `Successfully verified source of package`, even though the deployed and executing bytecode is actually `B`.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L9-12)
```markdown
- **Account publishing** (`publish` / `deploy`): the modules live at the **signer's account address**. Simplest pattern. Upgrades happen by re-publishing from the same account. The signer owns upgrade authority and can't transfer it.
- **Code-object publishing** (`deploy-object` / `upgrade-object`): the modules live at a **separate, derived object address**. Upgrade authority is held in a code object and can be transferred. Use this when modules need an address independent of any single account.

For code review and reproducibility, [`verify-package`](#aptos-move-verify-package) checks that on-chain bytecode matches a local source tree.
```

**File:** third_party/move/documentation/book/src/cli-deploy.md (L70-76)
```markdown
## `aptos move verify-package`

Build the package locally and verify that the on-chain copy matches.

```shellscript filename="Terminal"
aptos move verify-package --account 0xABC...123
```
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

**File:** third_party/move/tools/move-package/src/resolution/digest.rs (L12-51)
```rust
pub fn compute_digest(paths: &[PathBuf]) -> Result<PackageDigest> {
    let mut hashed_files = Vec::new();
    let mut hash = |path: &Path| {
        let contents = std::fs::read(path)?;
        hashed_files.push(format!("{:X}", Sha256::digest(&contents)));
        Ok(())
    };
    let mut maybe_hash_file = |path: &Path| -> Result<()> {
        match path.extension() {
            Some(x) if MOVE_EXTENSION == x => hash(path),
            _ if path.ends_with(SourcePackageLayout::Manifest.path()) => hash(path),
            _ => Ok(()),
        }
    };

    for path in paths {
        if path.is_file() {
            maybe_hash_file(path)?;
        } else {
            for entry in walkdir::WalkDir::new(path)
                .follow_links(true)
                .into_iter()
                .filter_map(|e| e.ok())
            {
                if entry.file_type().is_file() {
                    maybe_hash_file(entry.path())?
                }
            }
        }
    }

    // Sort the hashed files to ensure that the order of files is always stable
    hashed_files.sort();

    let mut hasher = Sha256::new();
    for file_hash in hashed_files.into_iter() {
        hasher.update(file_hash.as_bytes());
    }

    Ok(PackageDigest::from(format!("{:X}", hasher.finalize())))
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L55-65)
```text
    /// Metadata about a module in a package.
    struct ModuleMetadata has copy, drop, store {
        /// Name of the module.
        name: String,
        /// Source text, gzipped String. Empty if not provided.
        source: vector<u8>,
        /// Source map, in compressed BCS. Empty if not provided.
        source_map: vector<u8>,
        /// For future extensions.
        extension: Option<Any>,
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
