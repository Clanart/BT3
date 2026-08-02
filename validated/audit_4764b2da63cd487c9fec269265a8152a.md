### Title
`aptos move verify-package` never checks downloaded on-chain bytecode against local build, trusting self-reported `PackageMetadata` fields as ground truth - (File: aptos-move/cli/src/commands.rs, aptos-move/cli/src/stored_package.rs)

### Summary
The reported Lyra bug is a "trusted-but-unverified derived value" pattern: `strikeSkewGWAV` is silently overwritten with a clamped value while the true `skew` is stored elsewhere, so a downstream consumer (the GWAV oracle) trusts a value that no longer matches the real on-chain state. The Aptos-native analog is `aptos move verify-package`, which is documented and intended as the code-safety tool that "checks that on-chain bytecode matches a local compilation of the Move code" [1](#0-0) , but its implementation never fetches or compares actual bytecode — it only compares self-reported `PackageMetadata` struct fields.

### Finding Description
`VerifyPackage::execute` builds the package locally, then creates a `CachedPackageRegistry` with `with_bytecode` hard-coded to `false`: [2](#0-1) 

`CachedPackageRegistry::create` only downloads and stores actual on-chain module bytecode when `with_bytecode` is `true`; with it `false`, `self.bytecode` stays empty and no bytecode is ever fetched from the chain: [3](#0-2) 

The subsequent `package.verify(&compiled_metadata)` call, which is the only integrity check performed, compares only `PackageMetadata` fields (`name`, `deps`, `modules` — i.e. `ModuleMetadata { name, source, source_map, extension }` — `manifest`, `upgrade_policy`, `extension`, `source_digest`). It never touches actual module bytecode bytes: [4](#0-3) 

`ModuleMetadata.source`/`source_map` are optional, self-reported, gzip-compressed blobs supplied by the publisher at publish time via `PackageMetadata` [5](#0-4) , and `code::publish_package` only validates that the module *names* declared in the metadata match the modules actually contained in the bytecode bundle (`expected_modules`) — it never validates that the declared `source` text corresponds to the compiled bytecode that will actually execute on-chain: [6](#0-5) 

There is no cryptographic binding anywhere in `publish_package`/`request_publish`/`request_publish_with_allowed_deps` between `ModuleMetadata.source` and the actual `code: vector<vector<u8>>` bytecode bundle being written to storage [7](#0-6) . Consequently, a publisher can submit metadata whose `source` field is benign, readable Move source, while the accompanying bytecode bundle implements entirely different (malicious) logic; the module-name check and compatibility/dependency checks pass regardless, since none of those checks decode or compare source text to bytecode semantics.

`aptos move verify-package` is the tool users and auditors are expected to run to confirm "on-chain bytecode matches a local compilation" [8](#0-7) , yet its actual implementation performs a metadata-to-metadata comparison, not a metadata-to-bytecode or bytecode-to-bytecode comparison. This mirrors the report's root cause exactly: a downstream consumer of a supposedly-authoritative value (the source-verification claim / the GWAV cache) is fed a value (metadata equality) that has been decoupled from the actual ground truth (bytecode / true skew) by an intermediate step that never enforces the invariant it implicitly promises.

### Impact Explanation
Anyone relying on `aptos move verify-package` (or the underlying `CachedPackageRegistry`/`stored_package` APIs with `with_bytecode = false`) to confirm that on-chain code matches published source — auditors, dependency consumers relying on `deps`/policy-exempted trust, block explorers, or automated CI gating publish/upgrade decisions — receives a false "verified" result even when the actual bytecode differs arbitrarily from the claimed source. This is a code-safety/verification-bypass issue: it creates the exact "mismatch between verified bytes, package metadata... and committed module bytes" scenario called out as in-scope, and can mask malicious upgrades/publishes that would otherwise be caught by manual or automated source review. It does not, however, bypass the Move bytecode verifier or `check_upgradability`/compatibility checks that govern actual on-chain execution safety — the mismatch is specifically in the *auditing/verification tooling's* trust of self-reported metadata, not in consensus-critical state mutation itself.

### Likelihood Explanation
Likelihood is high in the sense that the code path always behaves this way — `with_bytecode: false` is unconditional in `VerifyPackage::execute`, so every invocation of `aptos move verify-package` is affected, and the `verify()` function structurally can never detect a source/bytecode mismatch since it has no bytecode field to compare. No special privileges are needed by an attacker; they need only publish a package whose declared `ModuleMetadata.source` differs from the compiled bytecode actually included in the bundle, which `code::publish_package`'s validation does not prevent.

### Recommendation
Have `VerifyPackage::execute` request `CachedPackageRegistry::create(client, self.account, true)` (fetch actual on-chain bytecode) and extend `CachedPackageMetadata::verify` (or add a companion check) to compare each downloaded module's bytecode bytes against the bytecode produced by the local build (`pack.extract_code()`), failing verification on any mismatch. Consider also documenting/removing the implication in `cli-deploy.md` that `verify-package` currently performs bytecode verification, until the bytecode-comparison path is implemented.

### Proof of Concept
1. Author a Move package where the source file for module `m` contains benign logic (e.g., a no-op `public fun f() {}`), and build `PackageMetadata` via `extract_metadata()` for this package — this yields `ModuleMetadata.source` gzip-compressed benign source.
2. Before submitting the publish transaction, replace the corresponding entry in the `code: vector<vector<u8>>` bundle passed to `code::publish_package`/`aptos_stdlib::code_publish_package_txn` with different, compiled bytecode for module `m` (e.g., bytecode with additional privileged logic), keeping the module name identical so `expected_modules` validation in `validate_publish_request` passes [9](#0-8) .
3. Publish the package; `publish_package` only checks module-name membership and compatibility/dependency policy, not source-to-bytecode correspondence [7](#0-6) , so publish succeeds.
4. Run `aptos move verify-package --account <addr>` using the original (benign) local source. Because `CachedPackageRegistry::create` is called with `with_bytecode: false` [10](#0-9) , no on-chain bytecode is fetched, and `package.verify(&compiled_metadata)` only compares metadata structs, which match (since the attacker used the same declared `ModuleMetadata.source`) [4](#0-3) . The tool reports `"Successfully verified source of package"` even though the deployed bytecode differs from the verified source.

**Confidence caveat:** I confirmed the code paths described above hold given the indexed portions of `commands.rs`, `stored_package.rs`, `code.move`, and `aptos_vm.rs`. I could not find any other call site (e.g., in `DownloadPackage`, `Disassemble`) that independently performs a bytecode-to-source comparison; the index did not return full contents for `DownloadPackage`/`Disassemble` command bodies, so I cannot rule out an alternate/undocumented bytecode-verification path elsewhere in the CLI with full certainty. Given the size limits of the codebase index, some file contents may not be available — a full-repository review via a Devin session would be needed for exhaustive confirmation.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L70-83)
```markdown
## `aptos move verify-package`

Build the package locally and verify that the on-chain copy matches.

```shellscript filename="Terminal"
aptos move verify-package --account 0xABC...123
```

| Flag | Meaning |
|---|---|
| `--account <ADDR>` | Address of the on-chain package to verify against. |
| `--included-artifacts <none\|sparse\|all>` | Match what was used at publish time. |

A package published with `upgrade_policy = "arbitrary"` cannot be verified — its content can change at any time, so the verifier refuses to depend on it.
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

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1816-1841)
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
            verify_module_metadata_for_module_publishing(m, self.features())
                .map_err(|err| Self::metadata_validation_error(&err.to_string()))?;
        }
```
