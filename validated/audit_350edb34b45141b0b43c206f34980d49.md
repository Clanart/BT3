### Title
`aptos move verify-package` never compares actual on-chain module bytecode, so published bytecode can silently diverge from the verified source/metadata - (File: `aptos-move/cli/src/stored_package.rs`)

### Summary
The Aptos CLI's `verify-package` command is documented as verifying "that on-chain bytecode matches a local source tree" [1](#0-0) , and `VerifyPackage::execute` fetches the on-chain `PackageRegistry`, builds the package locally, and calls `package.verify(&compiled_metadata)` to confirm it. [2](#0-1)  However, `CachedPackageRegistry::create` is invoked with `with_bytecode = false`, so the actual on-chain compiled module bytes are never downloaded. [3](#0-2)  The `verify` function itself only compares metadata fields — package name, `deps`, `modules` (which hold module name/compressed source/source-map/extension, not bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest` — and never fetches or diffs the real `.mv` bytecode against the freshly compiled bytecode. [4](#0-3) 

### Finding Description
On-chain publishing (`code::publish_package_txn`) accepts `metadata_serialized` (package/module metadata including human-readable source and source digest) and `code: vector<vector<u8>>` (the actual compiled module bytes) as two independent transaction parameters. [5](#0-4)  The framework only cross-checks that the *names* of modules in `code` match `expected_modules` derived from the metadata, plus compatibility/policy/dependency rules; it never binds the source text or source digest inside `PackageMetadata.modules[i].source`/`source_digest` to the actual bytecode blob being published. [6](#0-5) 

The only tool meant to close this gap for downstream consumers is `aptos move verify-package`, which is supposed to prove that what's on-chain matches a given source tree. But its implementation:
1. Calls `CachedPackageRegistry::create(client, self.account, false)` — the `false` disables bytecode retrieval entirely, so `bytecode: BTreeMap` stays empty. [7](#0-6) 
2. `CachedPackageMetadata::verify` only diffs `PackageMetadata`/`ModuleMetadata` struct fields (name, deps, modules metadata, manifest, upgrade_policy, extension, source_digest) between the on-chain registry entry and the locally rebuilt metadata. [4](#0-3) 
3. `get_bytecode`, which does fetch bytecode from the `bytecode` map populated only `with_bytecode = true`, is never called by `VerifyPackage::execute`. [8](#0-7) 

Because the metadata's `source`/`source_map`/`source_digest` fields are attacker-supplied at publish time and are not derived from or checked against the actual compiled module bytes by the Move framework, an account can publish one set of bytecode while embedding metadata (source, source_digest, manifest) that describes a completely different, benign-looking package. `verify-package` will rebuild that benign source locally, compare it to the attacker's metadata (which matches, since the attacker crafted it to match), and report `"Successfully verified source of package"` without ever having looked at the real bytecode that was actually stored on-chain and that will actually execute.

### Impact Explanation
This breaks the trust guarantee that `verify-package` is documented to provide "for code review and reproducibility." [9](#0-8)  Users, auditors, or automated tooling (e.g., before depositing funds into a contract, granting a resource-account signer capability, or approving a governance-adjacent module) that rely on a passing `verify-package` result to confirm deployed bytecode matches a reviewed/audited source tree can be misled into trusting malicious, unaudited bytecode. This is exactly the "mismatch between verified bytes, package metadata... and committed module bytes" class called out in the publish impact gate — the verifier declares success while the actually stored and executed module bytes are never checked.

### Likelihood Explanation
No privileged access is required. Any account (or resource account, or code object) can publish arbitrary bytecode alongside independently-crafted metadata describing different source, since the framework's `publish_package`/`request_publish_with_allowed_deps` path never validates that `PackageMetadata.modules[i].source`/`source_digest` corresponds to the actual `code` bytes. [10](#0-9)  The `verify-package` bug is deterministic and triggers on every invocation, since bytecode is unconditionally skipped (`with_bytecode = false`) with no code path in `VerifyPackage::execute` that ever fetches or compares it.

### Recommendation
In `VerifyPackage::execute` (`aptos-move/cli/src/commands.rs`), call `CachedPackageRegistry::create(client, self.account, true)` to fetch on-chain bytecode, and extend `CachedPackageMetadata::verify` (or add a companion check) in `aptos-move/cli/src/stored_package.rs` to fetch each module's on-chain bytes via `get_bytecode` and byte-compare them against `pack.extract_code()` output for the locally rebuilt package, failing verification on any mismatch, in addition to the existing metadata-field comparisons.

### Proof of Concept
1. Compile and publish package `P` with `code::publish_package_txn` where `code` contains malicious bytecode `M_bad`, but craft `metadata_serialized` so that `PackageMetadata.modules[0].source` (zipped) decompresses to a benign, reviewable Move source `S_good`, and set `source_digest` to the digest of `S_good`. Nothing in `code.move`/`aptos_vm.rs` cross-checks `S_good` against `M_bad`. [5](#0-4) 
2. A reviewer runs `aptos move verify-package --account <addr>` against a local checkout of `S_good`.
3. `VerifyPackage::execute` rebuilds `S_good` locally, fetches the `PackageRegistry` (no bytecode), and calls `package.verify(&compiled_metadata)`, which only compares the metadata fields — all of which match because they were crafted from `S_good`. [2](#0-1) 
4. The command prints `"Successfully verified source of package"`, even though the bytecode actually stored and executed on-chain is `M_bad`, not a compilation of `S_good`.

**Uncertainty note:** I could not directly inspect the full `ModuleMetadata`/`PackageMetadata` struct definitions in `aptos-move/framework/natives/src/code.rs` within the tool budget (only partial views via `code.move` were available), so I cannot 100% confirm there is no additional binding field (e.g., a per-module bytecode hash) that the framework enforces elsewhere. Based on all available evidence (`code.move`, `aptos_vm.rs::validate_publish_request`, and `stored_package.rs`), no such binding exists, but a full read of the native `ModuleMetadata` struct would give complete certainty.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L12-12)
```markdown
For code review and reproducibility, [`verify-package`](#aptos-move-verify-package) checks that on-chain bytecode matches a local source tree.
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L181-231)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L256-261)
```text
    /// Same as `publish_package` but as an entry function which can be called as a transaction. Because
    /// of current restrictions for txn parameters, the metadata needs to be passed in serialized form.
    public entry fun publish_package_txn(owner: &signer, metadata_serialized: vector<u8>, code: vector<vector<u8>>)
    acquires PackageRegistry {
        publish_package(owner, util::from_bytes<PackageMetadata>(metadata_serialized), code)
    }
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1818-1843)
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
