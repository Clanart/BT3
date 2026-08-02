## Finding: `aptos move verify-package` never compares on-chain bytecode, only self-reported metadata

I traced the "verify-package" flow, which is the Aptos-native analog of a bytecode/source verification tool (parallel to a Solidity contract-verification feature). This directly matches the required-impact bucket "Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes."

### Title
`aptos move verify-package` reports false verification success because it never checks deployed bytecode against the locally compiled bytecode - ([File: aptos-move/cli/src/commands.rs] / [File: aptos-move/cli/src/stored_package.rs])

### Summary
`VerifyPackage::execute` builds the package locally, fetches the on-chain `PackageRegistry` **without bytecode** (`with_bytecode: false`), and calls `CachedPackageMetadata::verify`, which only diff-checks the `PackageMetadata` struct fields (`name`, `deps`, `modules` metadata, `manifest`, `upgrade_policy`, `extension`, `source_digest`). At no point is the actual on-chain module bytecode (`0x1::code::...::bytecode`, obtainable via `get_account_module`) fetched and compared against the bytecode produced by the local build (`compiled_units`/`extract_code()`).

### Finding Description [1](#0-0) 

`VerifyPackage::execute` does:
1. Build the package locally to get `compiled_metadata`.
2. `CachedPackageRegistry::create(client, self.account, false)` — the third argument `with_bytecode` is `false`, so `bytecode: BTreeMap` stays empty (see `CachedPackageRegistry::create`, [2](#0-1) ).
3. `package.verify(&compiled_metadata)` — this only compares `PackageMetadata` fields, never bytecode: [3](#0-2) 

`CachedPackageRegistry` does have a `get_bytecode` accessor and the ability to download real deployed module bytes (`get_account_module`) when `with_bytecode` is `true`: [4](#0-3) 

but `VerifyPackage` passes `false` and never calls `get_bytecode`, so the actual deployed bytecode is never fetched or diffed against the freshly compiled bytecode.

The on-chain `PackageRegistry`/`PackageMetadata` published via `code::publish_package` (and `object_code_deployment::publish`/`upgrade`) is entirely publisher-supplied and is **not derived from, or checked against, the actual module bytes** by the VM — the `source`, `source_map`, and `source_digest` fields inside `ModuleMetadata`/`PackageMetadata` are just opaque payloads passed in by the publisher alongside the real bytecode (`code: vector<vector<u8>>`), with no protocol-level binding between them: [5](#0-4) 

Because of this, a publisher can submit `metadata_serialized` whose `source`/`source_digest`/`manifest` correspond to a benign, auditable source tree, while submitting `code` (the actual bytecode bundle) that implements entirely different logic. `aptos move verify-package`, which is the tool advertised in the docs to let a user "verify that the on-chain copy matches" a local build, would report success on such a package because it compares only the metadata struct — the exact metadata the malicious publisher forged — never the real bytecode: [6](#0-5) 

### Impact Explanation
This breaks the code-safety invariant that "package metadata... must describe the code that is actually verified and stored" (one of the stated publish pivots). Downstream consumers (auditors, governance voters, dApp integrators, block explorers relying on this CLI) who run `aptos move verify-package` to confirm that deployed bytecode matches an audited/expected source tree receive a false "Successfully verified source of package" result while the actually-executing bytecode can be arbitrary and malicious. This is a verification-bypass that undermines trust decisions (e.g., approving a dependency, granting a smart-contract wallet approval, or treating a package as verified before interacting with it) without requiring any special privilege from the attacker — any publisher can submit mismatched metadata and code.

### Likelihood Explanation
High likelihood of exploitation: any account can publish a package via `code::publish_package_txn`/`object_code_deployment::publish` with metadata bytes and code bytes that are unrelated to each other; there is no on-chain or off-chain (in this tool) check tying them together besides the flawed CLI verifier. No admin/governance privileges are needed.

### Recommendation
`VerifyPackage::execute` should call `CachedPackageRegistry::create(client, self.account, true)` (fetch bytecode) and additionally compare each module's downloaded bytecode (`get_bytecode`) byte-for-byte against the freshly compiled bytecode (`pack.extract_code()` / `compiled_units`) before reporting success. `CachedPackageMetadata::verify` should be extended to take the bytecode map and fail loudly on any mismatch, rather than relying solely on the publisher-supplied `source_digest`.

### Proof of Concept
1. Compile a benign package `A` locally and extract its `PackageMetadata` (`metadata_A`) — this yields correct `source`, `source_map`, `manifest`, `source_digest` for the *benign* source.
2. Compile a malicious package `B` (different logic, e.g., contains a backdoor) and extract its bytecode (`code_B`).
3. Publish with `code::publish_package_txn(metadata_A_serialized, code_B)` (or via `object_code_deployment::publish`). The Move VM only checks module addresses/compatibility/verifier rules on `code_B`; it never validates that `code_B` corresponds to `metadata_A`.
4. Run `aptos move verify-package --account <addr>` against local source `A`. Because `CachedPackageRegistry::create(..., false)` never downloads `code_B`, and `verify()` only compares `metadata_A` (on-chain) against locally rebuilt `metadata_A` (matches, since it's the same benign source), the tool prints `"Successfully verified source of package"` even though the live bytecode is `code_B`, the malicious/backdoored code.

### Citations

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

**File:** aptos-move/cli/src/stored_package.rs (L42-117)
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

    /// Returns the list of packages in this registry by name.
    pub fn package_names(&self) -> Vec<&str> {
        self.inner
            .packages
            .iter()
            .map(|p| p.name.as_str())
            .collect()
    }

    /// Finds the metadata for the given module in the registry by its unique name.
    pub async fn get_module(
        &self,
        name: impl AsRef<str>,
    ) -> anyhow::Result<CachedModuleMetadata<'_>> {
        let name = name.as_ref();
        for package in &self.inner.packages {
            for module in &package.modules {
                if module.name == name {
                    return Ok(CachedModuleMetadata { metadata: module });
                }
            }
        }
        bail!("module `{}` not found", name)
    }

    /// Finds the metadata for the given package in the registry by its unique name.
    pub async fn get_package(
        &self,
        name: impl AsRef<str>,
    ) -> anyhow::Result<CachedPackageMetadata<'_>> {
        let name = name.as_ref();
        for package in &self.inner.packages {
            if package.name == name {
                return Ok(CachedPackageMetadata { metadata: package });
            }
        }
        bail!("package `{}` not found", name)
    }

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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L157-230)
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
```

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
