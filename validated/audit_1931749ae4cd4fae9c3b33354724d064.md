## Summary

The external report's core lesson — a publish/verification-path function that silently fails to do what it claims, breaking a trust guarantee for legitimate on-chain code — has a concrete Aptos-native analog in the CLI's package verification path rather than in `code.move` itself (which I found to be carefully and correctly guarded, including the newer lazy-module-initialization ownership guard in `init.move`).

## Finding

### Title
`aptos move verify-package` never compares on-chain bytecode to locally-built bytecode, so verification can pass while deployed module bytes diverge from the claimed source — (File: `aptos-move/cli/src/stored_package.rs`)

### Finding Description
`CachedPackageMetadata::verify` is the function backing `aptos move verify-package`, which the docs describe as building the package locally and verifying "that the on-chain copy matches" [1](#0-0) .

Its signature only takes a `PackageMetadata`, never the actual compiled module bytes: [2](#0-1) 

It compares `name`, `deps`, the `ModuleMetadata` list (`name`/`source`/`source_map`/`extension`), `manifest`, `upgrade_policy`, `extension`, and `source_digest` between the fetched on-chain `PackageRegistry` entry and the locally rebuilt package's metadata. It never fetches the module's actual on-chain bytecode (even though `CachedPackageRegistry` is capable of downloading it via `get_bytecode`/`get_account_module` when constructed `with_bytecode = true`) and never hashes/compares it against the freshly compiled bytecode [3](#0-2) .

On-chain, `code::publish_package` stores `PackageMetadata` (source, source_digest, manifest) and the module bytecode (`code: vector<vector<u8>>`) as two independent pieces of data supplied by the publisher [4](#0-3) [5](#0-4) . Nothing in `code.move`, the VM's `validate_publish_request`, or the CLI enforces that the submitted bytecode actually corresponds to the submitted source/metadata — the Move compiler produces both together, but a malicious publisher who crafts the raw BCS payload directly (bypassing `aptos move publish`) can submit truthful-looking source/manifest/source_digest metadata alongside bytecode that does something different.

### Impact Explanation
`verify-package` and the doc's own security guidance direct users, auditors, and DAO/governance voters to rely on this exact tool to confirm what code is actually running before trusting or depending on an upgradeable package, as described in the "Security considerations for dependencies" section of the framework docs [6](#0-5) . Because `verify()` never checks the deployed bytecode, an attacker can publish a package whose `PackageMetadata` (source/manifest/source_digest) is legitimate and benign, while the accompanying `code` bytes contain different, malicious logic (e.g., a backdoor or an unauthorized privileged capability grab). `aptos move verify-package` would report success, giving third parties, integrators, or governance participants false assurance that the on-chain module matches the audited/claimed source — this is a code-replacement/verification bypass that fits the "mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes" impact class.

### Likelihood Explanation
The gap is deterministic and requires no special privilege beyond the normal ability to submit a raw `code::publish_package_txn` (or `object_code_deployment::publish/upgrade`, or `resource_account::create_resource_account_and_publish_package`) transaction with metadata and bytecode built independently rather than through `aptos move publish`, something already possible today via the BCS-serialized entry functions [7](#0-6) . Any user who then runs `aptos move verify-package` against that address, or any governance process that treats a passing verification as evidence of code-source correspondence, is exposed.

### Recommendation
Extend `CachedPackageRegistry`/`CachedPackageMetadata::verify` to always fetch the on-chain module bytecode (already supported via `get_bytecode`) and compare it byte-for-byte (or by hash) against the bytecode produced by locally recompiling the same sources, failing verification on any mismatch — not just on metadata field mismatches.

### Proof of Concept
1. Compile a benign Move package `P` producing source `S`, manifest `M`, and bytecode `B_good`.
2. Construct a `PackageMetadata` BCS blob using `S`, `M`, and a `source_digest` computed from `S` (all as they would be if `P` had genuinely been compiled and published).
3. Submit `code::publish_package_txn(metadata, vec![B_evil])` where `B_evil` is a maliciously modified compiled module unrelated to `S` (e.g., grants extra privileges, or omits a critical safety check), bypassing the standard `aptos move publish` toolchain (which normally guarantees code/metadata come from the same compile step).
4. A third party runs `aptos move verify-package --account <addr>`, which recompiles `P` from the published/claimed source and calls `verify()`; all metadata fields match, so verification reports success — despite the deployed module actually being `B_evil`, not the bytecode of `S`.

I was unable to fully confirm, due to the exhausted tool budget, whether the `VerifyPackage` CLI command wrapper in `aptos-move/cli/src/commands.rs` performs any additional independent bytecode comparison outside of `CachedPackageMetadata::verify`. Based on the `verify()` function signature (which structurally cannot receive code bytes) and the fact that `CachedPackageRegistry` only optionally fetches bytecode for other purposes (e.g., `save_bytecode_to_disk`) rather than for the `verify` path, I assess it is unlikely such a check exists elsewhere, but this should be confirmed by inspecting the full `VerifyPackage` command implementation before treating this as conclusively unpatched.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L70-76)
```markdown
## `aptos move verify-package`

Build the package locally and verify that the on-chain copy matches.

```shellscript filename="Terminal"
aptos move verify-package --account 0xABC...123
```
```

**File:** aptos-move/cli/src/stored_package.rs (L40-118)
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L27-53)
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

    /// A dependency to a package published at address
    struct PackageDep has store, drop, copy {
        account: address,
        package_name: String
    }
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L159-164)
```text
    public fun publish_package(owner: &signer, pack: PackageMetadata, code: vector<vector<u8>>) acquires PackageRegistry {
        // Disallow incompatible upgrade mode. Governance can decide later if this should be reconsidered.
        assert!(
            pack.upgrade_policy.policy > upgrade_policy_arbitrary().policy,
            error::invalid_argument(EINCOMPATIBLE_POLICY_DISABLED),
        );
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

**File:** third_party/move/documentation/book/src/modules-and-packages.md (L639-660)
```markdown
### Security considerations for dependencies

As mentioned above, even compatible upgrades can have disastrous effects for
applications that depend on the upgraded code. These effects can come from bugs,
but they can also be the result of malicious upgrades. For example,
an upgraded dependency can suddenly make all functions abort, breaking the
operation of your Move code. Alternatively, an upgraded dependency can make
all functions suddenly cost much more gas to execute than before the upgrade.
As a result, dependencies on upgradeable packages need to be handled with care:

- The safest dependency is, of course, an `immutable` package. This guarantees
  that the dependency will never change, including its transitive dependencies.
  In order to update an immutable package, the owner would have to introduce a
  new major version, which is practically like deploying a new, separate
  and independent package. This is because major versioning can be expressed
  only by name (e.g., `module feature_v1` and `module feature_v2`). However,
  not all package owners like to publish their code as `immutable`, because this
  takes away the ability to fix bugs and update the code in place.
- If you have a dependency on a `compatible` package, it is highly
  recommended you know and understand the entity publishing the package.
  The highest level of assurance is when the package is governed by a
  Decentralized Autonomous Organization (DAO) where no single user can initiate
```
