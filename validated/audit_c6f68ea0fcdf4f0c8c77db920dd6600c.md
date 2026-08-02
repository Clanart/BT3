## Title
`aptos move verify-package` (VerifyPackage) never compares on-chain bytecode, so it can report success on tampered/mismatched published modules - (File: aptos-move/cli/src/commands.rs, aptos-move/cli/src/stored_package.rs)

### Summary
The Aptos CLI's `aptos move verify-package` command is meant to confirm that the code actually stored on-chain at an address matches a local recompilation of the claimed source. In practice, the verification path never fetches or compares the deployed module bytecode; it only diffs the self-declared `PackageMetadata` (name, deps, source text blobs, manifest, upgrade policy, source digest). This mirrors the RedStone pattern of trusting unvalidated inputs (`basePrice`/`tokenPrice`) instead of cross-checking against the authoritative on-chain value — here the authoritative value is the compiled module bytecode, which is silently skipped.

### Finding Description
`VerifyPackage::execute` builds the package locally, then creates a `CachedPackageRegistry` with bytecode fetching disabled: [1](#0-0) 

```
let registry = CachedPackageRegistry::create(client, self.account, false).await?;
...
package.verify(&compiled_metadata)?;
```

`CachedPackageRegistry::create` only populates `self.bytecode` when `with_bytecode` is `true`: [2](#0-1) 

Since `VerifyPackage` passes `false`, `self.bytecode` stays empty and is never consulted by the "verify" call. `CachedPackageMetadata::verify` then only compares `PackageMetadata` struct fields — `name`, `deps`, `modules`, `manifest`, `upgrade_policy`, `source_digest`: [3](#0-2) 

Critically, `ModuleMetadata` (part of `modules`) only carries `name`, `source` (gzipped source text), `source_map`, and `extension` — it does **not** contain the actual compiled `.mv` bytecode: [4](#0-3) 

The real bytecode that executes on-chain is stored separately (fetched via `client.get_account_module`), and this field is exactly what `with_bytecode=true` would populate — but `VerifyPackage` never requests it, and even `verify()` has no code path that would use `self.bytecode` if it had been fetched. Consequently, the CLI's "source verification" only proves that the publisher's self-declared source text/manifest/policy match a local rebuild; it proves nothing about whether the deployed bytecode was actually produced from that source, nor whether it matches the declared `source_digest` at all. There is no on-chain or CLI-side step anywhere in this flow that recomputes the bytecode hash and compares it to what is actually loaded/executed at the address.

### Impact Explanation
This breaks the "verified bytes vs. committed module bytes" invariant that publish/verification tooling is expected to preserve. A package owner (or anyone who can influence what a user believes about a deployed package, e.g. by supplying misleading source alongside correctly-formed metadata) can have `aptos move verify-package` print `"Successfully verified source of package"` even though the bytecode actually stored and executed on-chain differs arbitrarily from the source shown to auditors/users. Since this is the primary tool referenced for confirming that deployed Move code matches audited source (a mainnet-relevant, code-safety-critical operation), the false-positive verification directly undermines trust decisions (e.g., users/integrators deciding to interact with or depend on a package believing its logic was audited-and-matched).

### Likelihood Explanation
No privileged access is required: this is a straightforward missing-check bug in the standard, permissionless CLI verification path that every non-framework user relies on to sanity-check on-chain code. It triggers on the normal `aptos move verify-package` invocation shown in `VerifyPackage::execute`, with no attacker interaction beyond publishing metadata whose `modules`/`manifest`/`source_digest` fields are internally consistent but whose bytecode is not derived from the shown source.

### Recommendation
- In `VerifyPackage::execute`, call `CachedPackageRegistry::create(client, self.account, true)` to fetch on-chain bytecode.
- Extend `CachedPackageMetadata::verify` (or add a new check) to recompile each module from source, compare its bytecode against `CachedPackageRegistry::get_bytecode`, and fail the verification if they differ.
- Additionally, verify that the declared `source_digest` is consistent with the actual published bytecode/source, rather than trusting the self-reported digest.

### Proof of Concept
1. Author a Move package where `source_digest`/`manifest`/module names in `PackageMetadata` are consistent with a legitimate-looking `source`, but the actually-published bytecode (compiled from different source) implements different logic.
2. Publish using standard `code::publish_package_txn`/`object_code_deployment::publish` — no on-chain check ties the bytecode back to `source`/`source_digest`.
3. Run `aptos move verify-package --account <addr>` pointing at a local build of the "legitimate-looking" source.
4. Observe `Ok("Successfully verified source of package")` from `VerifyPackage::execute` at [5](#0-4) , even though the on-chain bytecode was never fetched or compared (`with_bytecode=false`), demonstrating the verification is vacuous with respect to actual code safety.

### Citations

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
