## Finding

### Title
`aptos move verify-package` never compares on-chain bytecode to the local build, so it can attest to code it never actually verified - ([File: aptos-move/cli/src/stored_package.rs])

### Summary
The Aptos CLI's `verify-package` command is documented and intended to confirm that on-chain compiled bytecode matches a local, trusted source build. In implementation, it only compares `PackageMetadata` fields (name, deps, module source/source_map metadata, manifest, upgrade policy, extension, and a source-only digest) and never fetches or diffs the actual on-chain module bytecode. A package can therefore pass `verify-package` with "Successfully verified source of package" while the deployed bytecode differs arbitrarily from the verified source.

### Finding Description
`VerifyPackage::execute` builds the package locally, then creates a `CachedPackageRegistry` explicitly with `with_bytecode = false`: [1](#0-0) 

Because `with_bytecode` is `false`, `CachedPackageRegistry::create` never calls `client.get_account_module(...)` and the `bytecode` map stays empty: [2](#0-1) 

The subsequent `package.verify(&compiled_metadata)` call only compares `PackageMetadata` fields — `name`, `deps`, `modules` (which is `ModuleMetadata`: name/source/source_map/extension — source text, not bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest` (a hash over the *source files*, per the on-chain comment): [3](#0-2) [4](#0-3) 

At no point is the actual bytecode returned by `client.get_account_module` (which the `bytecode: bool` flag in `VerifyPackage`'s options, unused because of `false` here, would fetch) hashed or diff'd against the locally compiled `.mv` bytes. The command's own documentation and CLI help promise the opposite: [5](#0-4) [6](#0-5) 

This is structurally the same class of bug as the `extcodehash` report: the tool advertises that it accurately reproduces/attests a specific on-chain fact ("bytecode matches source"), but the actual check is computed from a different, weaker signal (source-text metadata and a source-only digest) that can diverge from the real, security-relevant value (the deployed module bytes).

### Impact Explanation
Any party that relies on `aptos move verify-package` (or the underlying `VerifyPackage`/`stored_package::verify` logic) to decide whether to trust, integrate with, or depend on an on-chain package can be misled: a publisher can put legitimate, auditable source and metadata on chain while the actually loaded/executed module bytecode is different (e.g., contains injected logic), and the verifier will still report success because it never looks at bytecode at all. Given Aptos's immutable-package trust model explicitly recommends verifying source before depending on `immutable` or `compatible` packages, a false "verified" attestation on a supply-chain-critical publish/upgrade path is a meaningful integrity failure with mainnet relevance.

### Likelihood Explanation
The divergence is deterministic and always present — not merely an edge case — because `VerifyPackage::execute` hardcodes `with_bytecode: false` in `CachedPackageRegistry::create`, so bytecode fetching/comparison never happens on any invocation of this command in this codebase version. Any user running the documented `verify-package` workflow is affected.

### Recommendation
Fetch the on-chain module bytecode (`with_bytecode = true`) in `VerifyPackage::execute`, recompute/compare it byte-for-byte (or via hash) against the locally compiled `.mv` bytes for every module in the package, and fail verification on any mismatch, in addition to the existing metadata checks in `CachedPackageMetadata::verify`.

### Proof of Concept
1. Publish a package `P` at address `A` with metadata (source, source_digest, manifest, deps) corresponding to legitimate source code `S`.
2. Craft `code: vector<vector<u8>>` bytecode that differs from what `S` compiles to (e.g., a backdoored version) but is still self-consistent for module names/expected_modules/compatibility as required by `code::publish_package`.
3. Run `aptos move verify-package --account A`, using local source `S`.
4. Observe: `CachedPackageRegistry::create(client, A, false)` skips bytecode download, `stored_package::verify` compares only metadata (identical, since metadata was crafted to match `S`), and the command prints `"Successfully verified source of package"` even though the deployed bytecode is not what was verified. [7](#0-6)

### Citations

**File:** aptos-move/cli/src/commands.rs (L2074-2076)
```rust
/// Downloads a package and verifies the bytecode
///
/// Downloads the package from onchain and verifies the bytecode matches a local compilation of the Move code
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L36-42)
```text
        /// The source digest of the sources in the package. This is constructed by first building the
        /// sha256 of each individual source, than sorting them alphabetically, and sha256 them again.
        source_digest: String,
        /// The package manifest, in the Move.toml format. Gzipped text.
        manifest: vector<u8>,
        /// The list of modules installed by this package.
        modules: vector<ModuleMetadata>,
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
