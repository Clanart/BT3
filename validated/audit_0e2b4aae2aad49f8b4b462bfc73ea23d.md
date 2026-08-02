The strongest local analog for this bug class is in the Aptos CLI's package verification path: `VerifyPackage::execute` and `CachedPackageMetadata::verify` never actually compare on-chain bytecode against the locally compiled bytecode, despite the command being documented and expected to "verify the bytecode matches a local compilation." This mirrors the external bug's pattern of a check that appears to guarantee an invariant (fully-backed debt / fully-verified code) but structurally cannot enforce it, leaving a state (bad debt / unverified bytecode) that downstream consumers wrongly trust.

### Title
`aptos move verify-package` reports code as verified while never comparing on-chain bytecode to compiled bytecode - (File: aptos-move/cli/src/stored_package.rs)

### Summary
The CLI command `VerifyPackage` is documented as "Downloads the package from onchain and verifies the bytecode matches a local compilation of the Move code" [1](#0-0)  but its implementation calls `CachedPackageRegistry::create(client, self.account, false)` with `with_bytecode = false`, so no on-chain module bytes are ever fetched, and it finishes by calling `package.verify(&compiled_metadata)` [2](#0-1) . `CachedPackageMetadata::verify` only compares self-reported `PackageMetadata` fields (`name`, `deps`, `modules` source text/source-map, `manifest`, `upgrade_policy`, `extension`, `source_digest`) — none of which is the compiled bytecode [3](#0-2) .

### Finding Description
`PackageMetadata.modules` (`ModuleMetadata`) stores only `name`, zipped `source`, zipped `source_map`, and `extension` — it does not carry, and is never required to correspond cryptographically to, the actual bytecode published via the separate `code: vector<vector<u8>>` argument to `code::publish_package_txn` / `request_publish_with_allowed_deps` [4](#0-3) . On-chain publishing only checks that module *names* in the metadata match the module names actually present in the bytecode bundle (`expected_modules` in `native_request_publish`) [5](#0-4) ; nothing ties the human-readable `source`/`source_map` metadata to the compiled bytes that were verified and stored.

Given that, `verify-package`'s job should be to download the actual `.mv` bytecode from chain and byte-compare it (or compare a hash of it) against a fresh local compile. Instead:
- `CachedPackageRegistry::create` is invoked with `with_bytecode = false`, so `self.bytecode` stays empty and no module bytes are ever downloaded in this flow [6](#0-5) .
- `verify()` never references `self.bytecode` or any bytecode at all; it strictly compares metadata struct fields, including the publisher-supplied `source` text that has no on-chain-enforced link to the compiled module bytes [7](#0-6) .

Since `PackageMetadata` (including the `source` text and `source_digest`) is provided by the publisher and stored as-is with no protocol-level requirement that it correspond to the actual `code` bundle bytes, a publisher (or upgrader on a `compat`/non-`immutable` package) can publish arbitrary bytecode alongside benign-looking `source`/`source_digest` metadata. Anyone relying on `aptos move verify-package` to confirm the on-chain module bytes match legitimate source will get a false "Successfully verified source of package" result [8](#0-7)  even though the actually-executing bytecode is unrelated to, or malicious relative to, the claimed source.

### Impact Explanation
This breaks the trust model documented for code-object/package publishing and for the CLI's `verify-package` docs (`third_party/move/documentation/book/src/cli-deploy.md`) which explicitly tells reviewers to use `verify-package` "to check that on-chain bytecode matches a local source tree" [9](#0-8) . Downstream consumers (auditors, dependent-package authors relying on `check_dependencies`'s upgrade-policy trust model, users approving transactions based on "verified" source) can be misled into trusting bytecode that was never actually verified byte-for-byte, enabling a form of unauthorized/undetected code substitution analogous to the bad-debt accrual continuing unchecked because the check that was supposed to gate it structurally never inspects the value that matters.

### Likelihood Explanation
High likelihood: this is the default, unconditional behavior of the shipped `verify-package` command (`with_bytecode: false` is hardcoded, not a flag) [10](#0-9) , so every invocation of this widely-documented security/reproducibility tool is affected, with no special conditions required from an attacker beyond publishing metadata whose `source`/`source_digest` fields don't reflect the real bytecode (which the protocol does not prevent).

### Recommendation
Update `CachedPackageRegistry::create` calls in `VerifyPackage::execute` to pass `with_bytecode = true`, and extend `CachedPackageMetadata::verify` (or a new bytecode-verification step) to fetch each module's on-chain bytes via `get_bytecode` and compare them (or their hash) against the freshly compiled `.mv` bytes for the same module, failing verification on any mismatch — not just on metadata-field mismatches.

### Proof of Concept
1. Compile and publish a package where module `m`'s bytecode implements malicious logic, but set `PackageMetadata.modules[0].source` (zipped) to benign source text whose compiled digest is crafted/copied to match `source_digest`, or simply publish through a flow that lets you set arbitrary metadata separate from `code` (e.g., `code::publish_package_txn(owner, metadata_serialized, code)` where `metadata_serialized` and `code` are independently supplied by the caller) [4](#0-3) .
2. Run `aptos move verify-package --account <addr>` using the benign local source tree.
3. Observe `Successfully verified source of package` printed [11](#0-10) , even though the on-chain bytecode (never fetched or compared) differs arbitrarily from what was "verified".

### Citations

**File:** aptos-move/cli/src/commands.rs (L2074-2076)
```rust
/// Downloads a package and verifies the bytecode
///
/// Downloads the package from onchain and verifies the bytecode matches a local compilation of the Move code
```

**File:** aptos-move/cli/src/commands.rs (L2119-2140)
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L256-261)
```text
    /// Same as `publish_package` but as an entry function which can be called as a transaction. Because
    /// of current restrictions for txn parameters, the metadata needs to be passed in serialized form.
    public entry fun publish_package_txn(owner: &signer, metadata_serialized: vector<u8>, code: vector<vector<u8>>)
    acquires PackageRegistry {
        publish_package(owner, util::from_bytes<PackageMetadata>(metadata_serialized), code)
    }
```

**File:** aptos-move/framework/natives/src/code.rs (L326-344)
```rust
    let mut expected_modules = BTreeSet::new();
    for name in safely_pop_arg!(args, Vec<Value>) {
        let str = get_move_string(name)?;

        // TODO(Gas): fine tune the gas formula
        context.charge(CODE_REQUEST_PUBLISH_PER_BYTE * NumBytes::new(str.len() as u64))?;
        expected_modules.insert(str);
    }

    let destination = safely_pop_arg!(args, AccountAddress);

    // Add own modules to allowed deps
    let allowed_deps = allowed_deps.map(|mut allowed| {
        allowed
            .entry(destination)
            .or_default()
            .extend(expected_modules.clone());
        allowed
    });
```

**File:** third_party/move/documentation/book/src/cli-deploy.md (L12-12)
```markdown
For code review and reproducibility, [`verify-package`](#aptos-move-verify-package) checks that on-chain bytecode matches a local source tree.
```
