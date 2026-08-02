## Title
`aptos move verify-package` never compares on-chain module bytecode; it only compares serialized metadata fields, allowing "verified" packages to run arbitrary bytecode that does not match the claimed source - (File: `aptos-move/cli/src/stored_package.rs`)

### Summary
The Optimism bug's core invariant is: a security-critical operation (`move()`) commits to a *target identity* (claim/position) that isn't cryptographically bound to the actual data being validated at execution time, so the check can silently validate the wrong thing. The Aptos-native analog I found is in the code-verification / publish-metadata trust chain: `aptos move verify-package` claims to prove that on-chain deployed bytecode matches a local, re-compiled version of the package's source, but the actual comparison never touches bytecode at all — it only compares the `PackageMetadata` struct fields (name, deps, module metadata, manifest, upgrade policy, extension, `source_digest`). The real on-chain compiled module bytes are never fetched for this check.

### Finding Description
`VerifyPackage::execute` in [1](#0-0)  builds the package locally, extracts `compiled_metadata`, downloads the on-chain `PackageRegistry` via `CachedPackageRegistry::create(client, self.account, false)` — note the explicit `false` for `with_bytecode` — and then calls `package.verify(&compiled_metadata)`.

`CachedPackageMetadata::verify` in [2](#0-1)  only compares: package `name`, `deps`, `modules` (a `ModuleMetadata` list containing `name`, gzipped `source`, `source_map`, `extension` — never bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest` (a hash of the *source files*, not of the compiled bytecode). Nowhere in this function is the actual `.mv` bytecode fetched from chain or compared to the freshly-compiled bytecode.

`CachedPackageRegistry::create` in [3](#0-2)  does have logic to fetch real bytecode via `client.get_account_module(...)` into `self.bytecode`, but only `if with_bytecode` is `true`. Since `VerifyPackage` passes `false`, `self.bytecode` is always empty for this command, and `get_bytecode()` — the only accessor that would expose the real module bytes — is never called by `verify()` or anywhere in `VerifyPackage::execute`.

`PackageMetadata.source_digest` is documented in `code.move` as "the sha256 of each individual source... sorted... and sha256 them again" [4](#0-3) , i.e. a hash over *source text*, not over the deployed bytecode. Nothing in the on-chain `code::publish_package` flow cryptographically binds `source_digest`/`ModuleMetadata.source` to the bytecode that is actually verified and stored by `request_publish`/`request_publish_with_allowed_deps` [5](#0-4) . A package owner can publish arbitrary bytecode (module A) under an account and independently craft `PackageMetadata` whose `modules[i].source` field contains innocuous/benign-looking source, as long as that fake source is itself internally consistent for name/manifest/source_digest purposes with what the auditor will locally recompile from (i.e. the metadata `verify()` check is symmetric: it just diffs the on-chain metadata blob against a locally regenerated metadata blob, it never asks "does this metadata actually describe the bytecode presently stored at this address?").

### Impact Explanation
This breaks the "mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes" invariant from the Publish Impact Gate. A user, auditor, or dependent contract owner running `aptos move verify-package` receives the message `"Successfully verified source of package"` — a strong correctness claim — while the tool has not confirmed that the source it examined has anything to do with the bytecode actually loaded and executed on-chain at that address. Since Aptos permits arbitrary/compatible upgrade policies and packages are frequently depended upon by other packages (`code::check_dependencies`), this creates a supply-chain risk: a malicious or compromised package owner can publish or upgrade code whose real bytecode diverges from its claimed/reviewed source, and this specific verification tool will falsely attest to a match. This is a code-safety/trust invariant failure directly in the publish verification pathway, matching the "Mismatch between verified bytes, package metadata... and committed module bytes" impact category.

### Likelihood Explanation
High likelihood of being hit unintentionally, and straightforward for a malicious actor to exploit deliberately, because:
- `with_bytecode: false` is hard-coded for `VerifyPackage`, so bytecode is *never* fetched regardless of user intent.
- Nothing in the CLI or on-chain `code.move` module cryptographically ties `source_digest`/module `source` metadata to the concrete published bytecode.
- The command is user-facing and its name/doc ("Downloads a package and verifies the bytecode matches a local compilation of the Move code" [6](#0-5) ) explicitly promises bytecode verification, so operators are likely to rely on its output as a security guarantee it does not actually provide.

### Recommendation
In `CachedPackageRegistry::create`, always fetch bytecode (or make `VerifyPackage` pass `with_bytecode: true`), and extend `CachedPackageMetadata::verify` to additionally recompile the local package to bytecode and byte-for-byte compare each module's on-chain bytecode (`self.bytecode`) against the freshly compiled module bytecode, not just the metadata struct. Consider also having `code::publish_package` commit a hash of the compiled bytecode bundle itself into `PackageMetadata` on-chain so any client-side verifier can trustlessly confirm bytecode/source correspondence without a full download-and-recompile-and-diff each time.

### Proof of Concept
1. Compile package `P` with source `foo.move` producing bytecode `B_good`, and craft an alternate malicious bytecode `B_evil` (e.g., adds a backdoor entry function) that still satisfies the same module name, same public function signatures acceptable to `code::check_upgradability`/`Compatibility`, and can be paired with the same `ModuleMetadata.source` (P's metadata `source` field is just gzipped text chosen by the publisher — the runtime `request_publish` native does not check `source`/`source_digest` against bytecode, see `native_request_publish` [7](#0-6)  which only inspects `bundle` (raw module bytes), `expected_modules`, and `allowed_deps` — never the `source`/`source_digest` metadata bytes).
2. Publish `B_evil` on-chain via `code::publish_package_txn` while setting `PackageMetadata.modules[0].source` to the gzip of `foo.move` (the benign source) and `source_digest` to sha256(foo.move) — the framework never checks that this digest corresponds to `B_evil`.
3. An auditor runs `aptos move verify-package --account <addr>` against a local checkout containing `foo.move`. `VerifyPackage::execute` recompiles `foo.move` to get `compiled_metadata`, fetches on-chain metadata with `with_bytecode=false`, and calls `verify()`, which compares `name`, `deps`, `modules` (source/source_map only), `manifest`, `upgrade_policy`, `extension`, `source_digest` — all of which match since the publisher deliberately crafted metadata to mirror `foo.move`.
4. The CLI prints `"Successfully verified source of package"` even though the deployed bytecode is `B_evil`, not the bytecode that `foo.move` compiles to.

Note: I was unable to fully trace whether any other Aptos tooling (e.g., Move Registry indexer, aptos.dev explorer verification) performs an independent bytecode-vs-source check that could mitigate reliance on this CLI command; that would require inspecting external/indexer services not present in this repo.

### Citations

**File:** aptos-move/cli/src/commands.rs (L2074-2076)
```rust
/// Downloads a package and verifies the bytecode
///
/// Downloads the package from onchain and verifies the bytecode matches a local compilation of the Move code
```

**File:** aptos-move/cli/src/commands.rs (L2098-2140)
```rust
#[async_trait]
impl CliCommand<&'static str> for VerifyPackage {
    fn command_name(&self) -> &'static str {
        "VerifyPackage"
    }

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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L366-390)
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
```
