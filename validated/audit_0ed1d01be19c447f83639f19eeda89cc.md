## Finding

<title>
`aptos move verify-package` never compares on-chain bytecode, only self-declared metadata — verification can be spoofed - (File: aptos-move/cli/src/commands.rs, aptos-move/cli/src/stored_package.rs)
</title>

### Summary
The Aptos CLI's `aptos move verify-package` command is documented as checking "that on-chain bytecode matches a local source tree" [1](#0-0) , but its implementation never fetches or compares the actual deployed module bytecode. It only compares self-declared `PackageMetadata` fields (name, deps, per-module source text/source-map, manifest, upgrade policy, source digest) between the on-chain registry entry and a fresh local build. Because `PackageMetadata` is supplied independently of, and is never cryptographically bound to, the actual `code: vector<vector<u8>>` bytes at publish time, an attacker can publish honest-looking metadata alongside arbitrary bytecode, and `verify-package` will still report success.

### Finding Description
`VerifyPackage::execute` creates the on-chain registry with `with_bytecode = false`: [2](#0-1) 

It only calls `package.verify(&compiled_metadata)`, which is defined in `stored_package.rs`: [3](#0-2) 

This function compares `name`, `deps`, `modules` (which is a `Vec<ModuleMetadata>` holding only `name`, gzipped `source`, `source_map`, and `extension` — not bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest`. None of these fields are, or contain, the actual compiled module bytes that execute on-chain. `CachedPackageRegistry::create` only downloads bytecode via `client.get_account_module` when `with_bytecode` is `true`, which `VerifyPackage` never requests: [4](#0-3) 

On the framework side, `code::publish_package` accepts `pack: PackageMetadata` and `code: vector<vector<u8>>` as two entirely independent transaction arguments with no on-chain binding between them beyond a module-name match: [5](#0-4) 

The only cross-check the VM performs at publish time between metadata and bytecode is that each `CompiledModule`'s own embedded name matches an entry in the metadata's declared module-name list: [6](#0-5) 

There is no check anywhere — neither on-chain nor in the CLI verifier — that the `source_digest` (a hash of the *source text*, per its own doc comment) or the gzipped `source` string in `ModuleMetadata` actually corresponds to the compiled bytecode. `source_digest` is explicitly documented as being derived purely from source text hashes: [7](#0-6) 

Consequently, a publisher can submit clean, auditable Move source/metadata (whose locally-rebuilt metadata will match byte-for-byte) while publishing a *different*, malicious `CompiledModule` bundle carrying the same module names. `aptos move verify-package` will report "Successfully verified source of package" even though the actually-running bytecode is unrelated to, and potentially malicious relative to, the claimed source.

### Impact Explanation
This breaks the core security property that `verify-package` is documented and relied upon to provide: proof that deployed code matches its claimed/audited source. Wallets, block explorers, governance reviewers, or third-party integrators that rely on `aptos move verify-package` (or reimplement its logic, since it is the canonical reference) to gain trust in a package before interacting with it, granting it capabilities, or listing it as "verified" would be misled. A malicious actor could publish a package with innocuous, publicly-auditable source metadata alongside a backdoored or entirely different bytecode payload and pass verification, enabling supply-chain-style deception of any downstream consumer of the "verified" status. This is a high-impact code-safety/verification bypass consistent with "mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes."

### Likelihood Explanation
Likelihood is high: exploitation requires no special privileges, race conditions, or governance access — any account publishing a package can supply mismatched metadata and bytecode, since the Move framework and native publish path never correlate them beyond module-name equality. The flaw is deterministic and always reproducible; it is not gated by any feature flag.

### Recommendation
- Extend `CachedPackageRegistry`/`VerifyPackage` to always fetch on-chain bytecode (`with_bytecode = true`) and compare each module's bytes byte-for-byte (or by hash) against the locally-compiled `CompiledModule` bytes, not merely metadata fields.
- Consider having `code::publish_package` (or a wrapping helper) cryptographically bind `source_digest`/`ModuleMetadata` to the actual bytecode hash on-chain, so any future verifier can detect metadata/bytecode divergence without needing a full rebuild-and-diff.
- Update `stored_package.rs::verify` to reject verification unless bytecode equality is established, and update CLI docs/output to make clear when bytecode was, or was not, actually compared.

### Proof of Concept
1. Author package `Foo` with benign `sources/foo.move` and publish it normally via `aptos move publish`, but before signing, swap the `code: vector<vector<u8>>` argument of the `code_publish_package_txn`/`object_code_deployment::publish` payload for bytecode of a different `CompiledModule` that shares the same module name (e.g., one containing a hidden admin backdoor), while leaving `metadata_serialized` (built from the benign source) untouched.
2. Submit the transaction. On-chain validation only checks that the swapped module's self-declared name matches the metadata's module-name list (`aptos_vm.rs::validate_publish_request`), which is trivially satisfiable, then stores both the honest metadata and the malicious bytecode.
3. Run `aptos move verify-package --account <addr>` from the benign source tree. `VerifyPackage::execute` fetches the registry with `with_bytecode=false`, rebuilds the benign source locally, and calls `package.verify(&compiled_metadata)`, which only compares metadata fields — all of which match the benign source — and prints "Successfully verified source of package", even though the deployed bytecode is the malicious swapped module.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L70-76)
```markdown
## `aptos move verify-package`

Build the package locally and verify that the on-chain copy matches.

```shellscript filename="Terminal"
aptos move verify-package --account 0xABC...123
```
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L36-38)
```text
        /// The source digest of the sources in the package. This is constructed by first building the
        /// sha256 of each individual source, than sorting them alphabetically, and sha256 them again.
        source_digest: String,
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L159-169)
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
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1816-1822)
```rust
        for m in modules {
            if !expected_modules.remove(m.self_id().name().as_str()) {
                return Err(Self::metadata_validation_error(&format!(
                    "unregistered module: '{}'",
                    m.self_id().name()
                )));
            }
```
