## Finding

### Title
`aptos move verify-package` never checks on-chain module bytecode, only self-reported metadata, allowing published code to diverge from "verified" source - ([File: aptos-move/cli/src/commands.rs], [File: aptos-move/cli/src/stored_package.rs])

### Summary
The CLI's `VerifyPackage` command is meant to prove that on-chain code matches a given source tree, but it only compares `PackageMetadata` struct fields (name, deps, module metadata, manifest, upgrade policy, source digest) — it never downloads or hashes the actual `.mv` module bytecode stored on-chain. Because `PackageMetadata` (including `source`, `manifest`, `source_digest`) is arbitrary, publisher-supplied data that the Move VM never cross-checks against the real bytecode bytes, an attacker can publish benign-looking metadata alongside different, malicious module bytecode, and `verify-package` will still report success.

### Finding Description
`VerifyPackage::execute` builds the package locally to obtain `compiled_metadata`, then fetches the on-chain registry **without bytecode** and calls `verify()`: [1](#0-0) 

`CachedPackageRegistry::create` is invoked with `with_bytecode = false`, so no module bytes (`.mv`) are ever fetched from the chain in this path: [2](#0-1) 

`CachedPackageMetadata::verify` then only diffs `PackageMetadata` fields — `name`, `deps`, `modules` (which is `ModuleMetadata`: name/gzipped source/source_map/extension, not bytecode), `manifest`, `upgrade_policy`, `extension`, `source_digest`: [3](#0-2) 

On the framework side, `code::publish_package_txn` stores whatever `PackageMetadata` the publisher passes in (including `source`/`source_digest`/`manifest`, which are simply gzipped blobs), completely independent from the `code: vector<vector<u8>>` bundle that is actually loaded as executable bytecode: [4](#0-3) 

The only on-chain cross-check between metadata and bytecode is that module *names* declared in metadata match module names extracted from the compiled bytecode (`expected_modules`) and, if dependency-checking is enabled, allowed dependency addresses — never that the embedded source text / source digest actually correspond to the published bytecode: [5](#0-4) 

So there is a real, exploitable gap: the on-chain protocol has no invariant tying `PackageMetadata.source`/`source_digest` to the actual bytecode, and the CLI verification tool — the only tool users have to check this correspondence — does not fetch bytecode at all, defeating its own stated purpose ("Downloads the package from onchain and verifies the bytecode matches a local compilation of the Move code", per its doc comment) despite never actually downloading bytecode in this code path.

### Impact Explanation
Downstream users, auditors, or dependent packages that rely on `aptos move verify-package` to confirm that deployed, executable module bytecode matches a reviewed/audited source tree receive a false "Successfully verified source of package" result even when the actually-published bytecode differs from (and can be malicious relative to) the claimed source. This directly matches the accepted category "Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes," and undermines a fundamental code-safety assumption (that verified metadata describes the code that is actually stored and executed).

### Likelihood Explanation
No special privilege is required. Any package publisher can supply metadata (`source`, `manifest`, `source_digest`) derived from one (benign) source tree while submitting an unrelated bytecode bundle in the same `publish_package_txn`/`object_code_deployment::publish` call — the framework only checks module names, not source correspondence. This can happen for every publish, and every invocation of `verify-package` against such a package will silently pass.

### Recommendation
- Have `VerifyPackage` always fetch on-chain bytecode (`with_bytecode = true`) and compare the SHA-256/BCS-serialized bytes of each locally compiled module against the corresponding on-chain module bytecode retrieved via `get_account_module`, rather than only comparing `PackageMetadata` struct fields.
- Consider tightening the doc/behavior gap: either rename/re-scope the command to make clear it only checks metadata self-consistency, or add a mandatory bytecode-hash comparison step so the "verify" result actually attests to code identity, not just metadata identity.

### Proof of Concept
1. Compile a benign package `Foo` locally; extract its `PackageMetadata` (`metadata_serialized`) via `BuiltPackage::extract_metadata()`.
2. Separately compile a malicious module with the same module name(s) as `Foo` (e.g. same `self_id()` names) but different logic; extract its bytecode.
3. Submit `code::publish_package_txn(owner, metadata_serialized_from_Foo, malicious_code_bytes)` (or the object-deployment equivalent). The VM only checks that module names in the bundle match `expected_modules` from the metadata and validates compatibility/dependencies — it never checks `metadata.modules[i].source`/`source_digest` against the bytecode, so this transaction succeeds.
4. Any third party runs `aptos move verify-package --account <addr>` against the original benign `Foo` sources. Because `VerifyPackage` never fetches on-chain bytecode (`with_bytecode=false`) and `verify()` only diffs metadata fields, and the attacker used `Foo`'s real metadata, the command prints `Successfully verified source of package` even though the deployed, executing bytecode is the malicious module.

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
