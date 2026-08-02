## Finding

This is an Aptos-native analog of the same root-cause pattern as the Sherlock report: a security check is *declared* but its enforcement is incomplete, so it silently passes when it should fail (or, here, silently gives a false "verified" result instead of catching a real bytecode mismatch).

### Title
`aptos move verify-package` never fetches or compares the on-chain compiled bytecode, so its "verified" result can be satisfied by code whose deployed bytecode differs from its claimed source — (File: `aptos-move/cli/src/commands.rs`, `aptos-move/cli/src/stored_package.rs`)

### Summary
The Aptos CLI's `VerifyPackage` command is documented and intended to confirm that on-chain published bytecode matches a fresh local compilation of the claimed source. In practice it only compares `PackageMetadata` fields (name, deps, module *source text*, manifest, upgrade policy, extension, source digest) — it never downloads or diffs the actual `.mv` bytecode that was published on-chain.

### Finding Description
`VerifyPackage::execute` creates the on-chain package registry view with bytecode fetching explicitly disabled: [1](#0-0) 

It then calls `package.verify(&compiled_metadata)`, whose full implementation only compares `name`, `deps`, `modules`, `manifest`, `upgrade_policy`, `extension`, and `source_digest`: [2](#0-1) 

The `modules` field being compared is `ModuleMetadata`, which only carries the gzip-compressed Move *source* and *source map*, not the compiled bytecode: [3](#0-2) 

Compare this with `DownloadPackage`, which *does* have a `bytecode: bool` flag and, when true, actually pulls each module's raw bytes via `registry.get_bytecode(module)`: [4](#0-3) 

`VerifyPackage` never sets or uses this bytecode-fetch path (`CachedPackageRegistry::create(client, self.account, false)`), so the comparison in `verify()` structurally cannot detect a mismatch between the actual deployed Move bytecode and what the (matching) source/metadata claims to represent. Yet the command's own doc-comment claims otherwise: "Downloads the package from onchain and verifies the bytecode matches a local compilation of the Move code," and the CLI reference doc similarly states it "verify[ies] that the on-chain copy matches" bytecode: [5](#0-4) [6](#0-5) 

This is directly analogous to the Sherlock bug class: a validation routine that is supposed to gate trust on a specific artifact (contract bytecode / target address) but whose implementation omits the check that actually inspects that artifact, causing the safety check to succeed even when the underlying invariant (published bytecode == claimed source) is violated.

### Impact Explanation
`verify-package` is the tool third parties (auditors, dependent-package integrators, exchanges, and end users doing due diligence) are expected to use to confirm that a deployed Aptos module's on-chain bytecode corresponds to publicly reviewed source. Because the tool only diffs metadata/source text and never diffs actual bytecode, a package owner can publish bytecode that diverges from the source text stored in `PackageMetadata.modules[].source` (e.g., by hand-crafting/patching the module bytes while keeping the embedded gzip source and manifest identical) and still have `aptos move verify-package` report `"Successfully verified source of package"`. This is a code-authenticity/verification bypass — exactly the "mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes" scenario called out as in-scope, and it undermines the trust signal used for code-safety decisions around packages consumers may treat as verified/safe to depend on or interact with.

### Likelihood Explanation
No special privileges are required beyond the ordinary ability to publish/upgrade a package (which any account already has for its own address). The bug is deterministic and always reachable: any user who runs `verify-package` gets a false-positive "verified" result whenever bytecode was mutated independent of the embedded source text/metadata, since the comparison path structurally excludes bytecode.

### Recommendation
Make `VerifyPackage` fetch on-chain bytecode (as `DownloadPackage` already supports) for every module and compare it byte-for-byte (or by hash) against a fresh local compilation's serialized module bytes, in addition to the existing metadata comparison in `CachedPackageMetadata::verify`.

### Proof of Concept
1. Publish package `P` at address `A` with metadata whose `modules[].source` is legitimate benign source `S`.
2. Craft or hand-patch the actual `.mv` bytecode bundle passed to `code::publish_package_txn`/`object_code_deployment::publish` so that it behaves differently from `S` (e.g., different logic in a function), while still declaring the same `ModuleMetadata.source = gzip(S)` in the package metadata blob.
3. Run `aptos move verify-package --account A` from a machine that has the genuine source `S`.
4. Observe the command locally rebuilds `S` and compares only `PackageMetadata` fields (`aptos-move/cli/src/stored_package.rs:193-241`) — since `source`, `manifest`, `deps`, `name`, `upgrade_policy`, `source_digest` all match, the command prints "Successfully verified source of package" despite the deployed bytecode being different from `S`.

### Citations

**File:** aptos-move/cli/src/commands.rs (L2035-2064)
```rust
    async fn execute(self) -> CliTypedResult<&'static str> {
        let client = self.rest_options.client(&self.profile_options)?;
        let registry = CachedPackageRegistry::create(client, self.account, self.bytecode).await?;
        let output_dir = dir_default_to_current(self.output_dir)?;

        let package = registry
            .get_package(self.package)
            .await
            .map_err(|s| CliError::CommandArgumentError(s.to_string()))?;
        if package.upgrade_policy() == UpgradePolicy::arbitrary() {
            return Err(CliError::CommandArgumentError(
                "A package with upgrade policy `arbitrary` cannot be downloaded \
                since it is not safe to depend on such packages."
                    .to_owned(),
            ));
        }
        if self.print_metadata {
            println!("{}", package);
        }
        let package_path = output_dir.join(package.name());
        package
            .save_package_to_disk(package_path.as_path())
            .map_err(|e| CliError::UnexpectedError(format!("Failed to save package: {}", e)))?;
        if self.bytecode {
            for module in package.module_names() {
                if let Some(bytecode) = registry.get_bytecode(module).await? {
                    package.save_bytecode_to_disk(package_path.as_path(), module, bytecode)?
                }
            }
        };
```

**File:** aptos-move/cli/src/commands.rs (L2074-2076)
```rust
/// Downloads a package and verifies the bytecode
///
/// Downloads the package from onchain and verifies the bytecode matches a local compilation of the Move code
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

**File:** third_party/move/documentation/book/src/cli-deploy.md (L70-76)
```markdown
## `aptos move verify-package`

Build the package locally and verify that the on-chain copy matches.

```shellscript filename="Terminal"
aptos move verify-package --account 0xABC...123
```
```
