## Finding: `aptos move verify-package` Never Compares Actual On-Chain Bytecode to Locally Compiled Bytecode

### Title
`VerifyPackage` CLI command falsely reports "verified" packages by comparing only self-declared metadata, never the committed on-chain module bytes - (File: `aptos-move/cli/src/commands.rs`, `aptos-move/cli/src/stored_package.rs`)

### Summary
The `aptos move verify-package` command is documented as checking "that on-chain bytecode matches a local source tree" [1](#0-0)  but its implementation never fetches or compares the actual on-chain module bytecode against the locally recompiled bytecode. It only diffs `PackageMetadata` fields — most of which (module source strings, source maps, manifest text) are opaque, publisher-supplied bytes stored at publish time with no on-chain binding to the actual compiled module bytes.

### Finding Description
`VerifyPackage::execute` builds the package locally, extracts its `PackageMetadata`, fetches the on-chain `PackageRegistry` via `CachedPackageRegistry::create(client, self.account, false)` — note `with_bytecode = false` — and then calls `package.verify(&compiled_metadata)`: [2](#0-1) 

`CachedPackageRegistry::create` only populates `self.bytecode` when `with_bytecode` is `true`; here it is `false`, so no module bytecode is ever downloaded from chain: [3](#0-2) 

`CachedPackageMetadata::verify` then compares only: `name`, `deps`, `modules` (which contains publisher-supplied zipped `source`/`source_map` strings, not bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest` (a hash computed by the local compiler over the *source files*, not over the module bytes): [4](#0-3) 

Nothing in this path calls `get_bytecode`/`get_account_module` and compares it byte-for-byte with `pack.extract_code()`. Since `code::publish_package` in the Move framework does not itself enforce that the embedded `ModuleMetadata.source` corresponds to the actual bytecode bundle being published — the `source`/`source_map` fields are informational and freely settable by the publisher — a malicious package owner can publish bytecode `B_malicious` together with metadata whose `modules[].source` field contains the pretty, benign-looking source `S_legit`, while `deps`/`manifest`/`upgrade_policy` are set to plausible legitimate values. `verify-package` will then successfully rebuild `S_legit` locally, get matching `PackageMetadata`, and print `"Successfully verified source of package"` even though the bytecode actually installed on-chain does not correspond to that source at all.

### Impact Explanation
This breaks the code-safety invariant that "verified bytes, package metadata, dependency declarations, and committed module bytes" must agree, which is explicitly called out as an in-scope Publish Impact. A downstream consumer, auditor, wallet, or dependent package developer relying on `aptos move verify-package` to confirm that a deployed package's logic matches its published source has no actual guarantee of that — they could be fooled into trusting or depending on a package whose real bytecode implements arbitrary/malicious logic while displaying an innocuous source tree. This is a code-safety/verification bypass with high real-world consequence, since `verify-package` is the only tool in this repo offered for exactly this trust decision (see `cli-deploy.md`'s stated purpose).

### Likelihood Explanation
No privileged access is required. Any account can publish a package (permissionless publish flow via `code::publish_package_txn`), and nothing on-chain forces the embedded `ModuleMetadata.source`/`source_map` fields to be produced by compiling the actual published bytecode. Any attacker who controls a publish transaction can trivially set these metadata fields to arbitrary bytes matching a benign source tree while shipping different bytecode, and the flaw in `verify-package` (never fetching/comparing real bytecode) is unconditional — it triggers on every invocation, not a rare edge case.

### Recommendation
In `VerifyPackage::execute`, call `CachedPackageRegistry::create(client, self.account, true)` to fetch on-chain module bytecode, then compare each module's downloaded bytes (`get_bytecode`) byte-for-byte against `pack.extract_code()` for the locally built package, in addition to (not instead of) the existing metadata comparison in `CachedPackageMetadata::verify`. Fail verification if any module's on-chain bytecode does not exactly match the freshly compiled bytecode.

### Proof of Concept
1. Compile and publish a package where `code` (the actual `vector<vector<u8>>` bytecode argument to `code::publish_package_txn`) is compiled from malicious source `S_evil`, but craft/serialize the `PackageMetadata` argument's `modules[].source` (and `source_map`) fields to contain the zipped source of an innocuous package `S_legit` instead (this is possible because `publish_package_txn` deserializes an attacker-supplied `metadata_serialized` blob with `util::from_bytes<PackageMetadata>`; see `aptos-move/framework/aptos-framework/sources/code.move:258-261`), while keeping `deps`, `manifest`, `upgrade_policy` consistent with `S_legit`.
2. Locally, compile `S_legit` and run `aptos move verify-package --account <attacker_addr>`.
3. Observe that `VerifyPackage::execute` calls `CachedPackageRegistry::create(client, account, /*with_bytecode=*/false)` (`aptos-move/cli/src/commands.rs:2121`), so no bytecode is downloaded, and `package.verify(&compiled_metadata)` (`aptos-move/cli/src/stored_package.rs:193`) only diffs metadata fields, which all match `S_legit`.
4. The command prints `"Successfully verified source of package"` even though the actually installed module bytecode implements `S_evil`, demonstrating the mismatch between committed module bytes and the "verified" claim.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L12-12)
```markdown
For code review and reproducibility, [`verify-package`](#aptos-move-verify-package) checks that on-chain bytecode matches a local source tree.
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
