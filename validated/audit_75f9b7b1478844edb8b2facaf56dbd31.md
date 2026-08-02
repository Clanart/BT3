## Title
`aptos move verify-package` never checks that downloaded module bytecode matches on-chain code — verification only compares self-reported metadata - (File: aptos-move/cli/src/stored_package.rs)

### Summary
The external report describes a value-mismatch bug in a DeFi router: the contract's accounting believed one value (the full input amount was spent) while the real, executed value was different (a partial fill), and no code path reconciled the two — leaving funds exposed. The Aptos-native analog is in the code-verification/publish-trust path: `PackageMetadata` (including `source_digest`, `manifest`, `modules` source text) is entirely self-reported by the publisher at publish time and is never checked on-chain against the actual bytecode bytes being stored. The CLI's `verify-package` command, whose entire purpose is to let a third party confirm "on-chain bytecode matches local source," only compares metadata-to-metadata and explicitly skips fetching/comparing the real bytecode.

### Finding Description
`PackageMetadata` is submitted by the publisher as part of the publish transaction and stored verbatim in the `PackageRegistry` resource: [1](#0-0) . On-chain publish validation (`validate_publish_request` in `aptos_vm.rs`) only checks that (a) every module name declared in `expected_modules` is actually published, and (b) declared dependencies are consistent with the bytecode's real dependencies via `allowed_deps` [2](#0-1) . Nothing on-chain verifies that `source_digest`, `manifest`, or `ModuleMetadata.source` actually correspond to the compiled bytecode bytes being published — these fields are purely descriptive/self-reported.

The CLI's `verify-package` command is the designated tool users are told to rely on for "code review and reproducibility... checks that on-chain bytecode matches a local source tree" (per the CLI docs) [3](#0-2) . Its implementation:

```
let registry = CachedPackageRegistry::create(client, self.account, false).await?;
...
package.verify(&compiled_metadata)?;
``` [4](#0-3) 

`CachedPackageRegistry::create` is called with `with_bytecode = false`, so the actual on-chain module bytecode is never downloaded via `get_account_module` [5](#0-4) . The subsequent `verify()` function only compares `name`, `deps`, `modules` (the `ModuleMetadata` struct — name/source-text/source-map, not bytecode hash), `manifest`, `upgrade_policy`, `extension`, and `source_digest` fields between the on-chain metadata and a freshly-built local package's metadata: [6](#0-5) . At no point is `self.bytecode` (which would hold real on-chain bytes if fetched) compared against the compiled module bytes produced by `BuiltPackage::build_to`.

Because `source_digest`, `manifest` bytes, and per-module `source`/`source_map` fields are all publisher-controlled inputs to `publish_package`/`publish_package_txn` and are never cross-checked against the bytecode by the VM, a malicious publisher can submit:
- bytecode `B` (attacker's real, malicious logic), and
- `PackageMetadata` whose `source_digest`/`manifest`/`modules[].source` are copied verbatim from a legitimate, benign package `S` (which the attacker can freely compute offline, since `source_digest` is just `sha256` over source files).

Any third party who runs `aptos move verify-package` against this account, using the legitimate source `S`, will get `Successfully verified source of package` even though the deployed bytecode `B` has nothing to do with `S`, because the tool never looks at `B`.

### Impact Explanation
This breaks the "verified bytes ↔ committed module bytes" invariant explicitly called out in the audit scope. `verify-package` is the only tool the framework ships for confirming that on-chain code corresponds to reviewed/audited source. An attacker (or a malicious/compromised deployer) can publish arbitrary, unaudited bytecode while making it pass `verify-package` against innocuous source code that was actually audited/reviewed by users, wallets, explorers, or automated tooling that shells out to this CLI command. This can facilitate supply-chain style attacks: reviewers, integrators, or automated CI that gate trust decisions (e.g. "only interact with contracts that pass `verify-package`") can be fooled into treating malicious bytecode as verified, safe code. This is a code-safety/publish-verification bypass with real mainnet relevance since `verify-package` is a documented, user-facing trust mechanism.

### Likelihood Explanation
High likelihood: the publisher of a package fully controls all `PackageMetadata` fields (including `source_digest`), and nothing in `code.move`, the native `request_publish`/`request_publish_with_allowed_deps` functions, or `aptos_vm.rs`'s `validate_publish_request` cross-checks metadata source fields against actual bytecode content. Exploiting this requires no special privilege beyond being any permissionless publisher — no admin/governance capability is needed. The bug is a straightforward omission (`with_bytecode: false` plus a `verify()` method that never touches bytecode at all), not a race condition or timing-dependent condition, so it is reliably reproducible on any account/package.

### Recommendation
- In `VerifyPackage::execute`, call `CachedPackageRegistry::create(client, self.account, true)` to fetch actual on-chain bytecode for every module.
- In `CachedPackageRegistry::verify` (or a new method), additionally compare each on-chain module's fetched bytecode bytes (via `get_bytecode`) against the freshly-compiled `compiled_units` bytecode produced from the local source tree, and fail verification on any mismatch — not just metadata field mismatches.
- Consider also strengthening trust: on-chain, none of `source_digest`/`manifest`/`ModuleMetadata.source` is currently checked against bytecode; documentation should make explicit that these fields carry no cryptographic guarantee, and any off-chain verification tool must always independently re-derive/verify bytecode, not just metadata equality.

### Proof of Concept
1. Attacker writes malicious Move source `Malicious.move` implementing module `0xCAFE::m` with harmful logic, and compiles it to bytecode `B`.
2. Attacker separately builds a legitimate, benign package `S` (e.g., a widely-audited open source Move package) that also declares a module named `m` with the same expected module name set, computes its `PackageMetadata` (via `BuiltPackage::extract_metadata()`), which includes `source_digest`, `manifest` bytes, and `ModuleMetadata.source`/`source_map` for `S`.
3. Attacker submits a publish transaction with `code = [B]` (malicious bytecode) but `metadata = metadata_of(S)` (legitimate metadata), via `code::publish_package_txn(owner, metadata_serialized_of_S, [B])`. On-chain validation only checks that a module named `m` is present in the bundle and its `expected_modules`/dependency declarations match `S`'s metadata declared deps — it never inspects `ModuleMetadata.source`, `manifest`, or `source_digest` against `B`, so the transaction succeeds: [2](#0-1) .
4. A reviewer/integrator downloads `S`'s source, runs `aptos move verify-package --account <attacker_addr>` from within `S`'s package directory.
5. `VerifyPackage::execute` builds `S` locally, extracts its `compiled_metadata`, fetches only the on-chain `PackageRegistry` metadata (`with_bytecode = false`), and calls `package.verify(&compiled_metadata)` [7](#0-6) . Since the on-chain metadata is byte-for-byte the metadata of `S`, every field comparison in `verify()` succeeds and the command prints `"Successfully verified source of package"`, even though the deployed bytecode is actually the malicious `B`.

Note: I could not execute this end-to-end (no filesystem/CLI access in ask-only mode) to observe the literal CLI output, so this PoC is derived purely from static tracing of the code paths cited above; independent confirmation via a live `aptos move verify-package` run against a crafted account is recommended before treating this as fully validated.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/code.move (L27-47)
```text
    /// Metadata for a package. All byte blobs are represented as base64-of-gzipped-bytes
    struct PackageMetadata has copy, drop, store {
        /// Name of this package.
        name: String,
        /// The upgrade policy of this package.
        upgrade_policy: UpgradePolicy,
        /// The numbers of times this module has been upgraded. Also serves as the on-chain version.
        /// This field will be automatically assigned on successful upgrade.
        upgrade_number: u64,
        /// The source digest of the sources in the package. This is constructed by first building the
        /// sha256 of each individual source, than sorting them alphabetically, and sha256 them again.
        source_digest: String,
        /// The package manifest, in the Move.toml format. Gzipped text.
        manifest: vector<u8>,
        /// The list of modules installed by this package.
        modules: vector<ModuleMetadata>,
        /// Holds PackageDeps.
        deps: vector<PackageDep>,
        /// For future extension
        extension: Option<Any>
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
