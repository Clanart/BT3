## Analysis Result

### Title
`aptos move verify-package` never compares actual on-chain module bytecode, allowing verified-mismatched code to pass as "verified" - (File: `aptos-move/cli/src/commands.rs`, `aptos-move/cli/src/stored_package.rs`)

### Summary
The external report's core invariant is: *a system that claims to validate a piece of on-chain data must actually check the real, current bytes, not a self-reported/stale proxy for them*. In `ChainlinkAtlasWrapper`, the proxy was "trusted transmitter signatures" instead of "was this exact report already used." In Aptos, the analogous publish-path proxy is `PackageMetadata` (self-reported name/deps/source/source_digest) standing in for the actual immutable module bytecode stored under `0x1::code::PackageRegistry`.

### Finding Description
The CLI command `VerifyPackage` is documented as: *"Downloads a package and verifies the bytecode... verifies the bytecode matches a local compilation of the Move code"* [1](#0-0) .

In its implementation, it builds the package locally, extracts `PackageMetadata` via `extract_metadata()`, fetches the on-chain registry with `CachedPackageRegistry::create(client, self.account, false)` — note the `false` argument for `with_bytecode` — and then calls `package.verify(&compiled_metadata)` [2](#0-1) .

Because `with_bytecode` is `false`, `CachedPackageRegistry::create` never fetches the module's actual bytecode from `get_account_module` and the internal `bytecode: BTreeMap<String, Vec<u8>>` stays empty [3](#0-2) .

`CachedPackageMetadata::verify()` only compares `PackageMetadata` struct fields — `name`, `deps`, `modules` (which only carries `name`, gzipped `source`, `source_map`, not raw bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest` [4](#0-3) . It never touches `registry.get_bytecode(...)` or the on-chain `Code` resource bytes at all — the function that could do this (`get_bytecode`) exists [5](#0-4)  but is simply not invoked from the `verify-package` path.

On-chain, `code::publish_package` and the `request_publish`/`request_publish_with_allowed_deps` natives never validate that `code: vector<vector<u8>>` corresponds to `source_digest` or the module's declared `source` — the Move VM verifier only checks module-address-matches-sender, backward compatibility, and dependency policy, not any hash linkage from metadata to bytecode [6](#0-5) . This is by design (metadata is documentational), which makes the CLI-level `verify-package` the *only* checkpoint a downstream consumer has to confirm that "the bytecode a publisher deployed matches the source they claim to have deployed" — and that checkpoint is broken.

### Impact Explanation
A malicious or compromised package publisher can: (1) publish an honest-looking `PackageMetadata` (correct `name`, `manifest`, `deps`, module `source` text, and a `source_digest` computed over that honest source) together with (2) a *different*, malicious compiled bytecode bundle in the same `publish_package_txn`/`object_code_deployment::publish` transaction. Nothing on-chain enforces that the bytecode is the actual compilation output of the declared source. A downstream user or auditor running `aptos move verify-package` to confirm "this on-chain module matches the published open-source code" will get `"Successfully verified source of package"` even though the deployed bytecode is entirely different from what was reviewed, because the tool never downloads or diffs the real bytecode. This directly matches the Publish Impact Gate's "Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes" — the disclosure and trust workflow that users rely on before interacting with a contract is defeated, enabling silent malicious code execution under the guise of a "verified" package.

### Likelihood Explanation
High likelihood of being reachable and misleading: `verify-package` is a documented, user-facing CLI workflow (`third_party/move/documentation/book/src/cli-deploy.md` describes it as the tool to "Build the package locally and verify that the on-chain copy matches" [7](#0-6) ), so it is exactly the workflow third parties (wallets, explorers, auditors, integrators) are expected to run before trusting a contract. No special privilege is required by the attacker — any unprivileged account publishing a package can supply mismatched metadata/bytecode, since nothing prevents it. The bug requires zero cooperation from the verifier's operator; it's a pure client-side/tooling gap, not a consensus safety break, but it undermines the actual security guarantee the tool advertises.

### Recommendation
`CachedPackageRegistry::create` should be invoked with `with_bytecode = true` inside `VerifyPackage::execute`, and `CachedPackageMetadata::verify()` (or a new verification step) must additionally: fetch each module's actual on-chain bytecode via `get_bytecode`, recompute/compare it byte-for-byte (or via hash) against the freshly locally-compiled bytecode (`BuiltPackage::extract_code()`), and fail verification on any mismatch — not just on metadata-field mismatches. This closes the gap between "metadata says X" and "bytecode actually is X," mirroring the recommended fix pattern in the seed report (ensure the thing being trusted is verified against its authoritative, current state rather than a self-reported proxy).

### Proof of Concept
1. Compile a benign Move package `pack_honest` with module `M`. Compute `metadata = extract_metadata()` (includes gzipped source of `M` and its `source_digest`).
2. Compile a second, malicious module `M_evil` with the *same* module name/address but different logic (e.g., an added backdoor entry function), keeping the `PackageMetadata`'s declared `source`/`source_digest` from step 1 unchanged (metadata is attacker-controlled input to `publish_package_txn`).
3. Submit `code::publish_package_txn(&signer, metadata_serialized_from_step_1, code_bundle_from_step_2)`. The VM only checks module-address-matches-sender and (if not first publish) compatibility/dependency policy — it does not check `source_digest` against `code` — so this transaction succeeds and `M_evil`'s bytecode is stored on-chain under metadata describing `pack_honest`.
4. A third party runs `aptos move verify-package --account <addr>` after independently building `pack_honest` locally. Since `VerifyPackage::execute` calls `CachedPackageRegistry::create(client, account, false)` and `verify()` only diffs `PackageMetadata` fields (identical to what was submitted in step 3), the command prints `"Successfully verified source of package"` even though the actual deployed bytecode (`M_evil`) never matches `pack_honest`.

**Note on confidence**: I was able to confirm the code paths described above via static reading of `commands.rs` and `stored_package.rs`, but I did not execute the CLI or a live e2e test to empirically confirm the `verify-package` output string in a running environment; this is a static-analysis-based finding on the tool's logic.

### Citations

**File:** aptos-move/cli/src/commands.rs (L2074-2077)
```rust
/// Downloads a package and verifies the bytecode
///
/// Downloads the package from onchain and verifies the bytecode matches a local compilation of the Move code
#[derive(Parser)]
```

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
