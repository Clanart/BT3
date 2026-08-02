## Title
`aptos move verify-package` never compares actual on-chain bytecode — reports false verification success (File: `aptos-move/cli/src/commands.rs`, `aptos-move/cli/src/stored_package.rs`)

### Summary
The Aptos CLI's `verify-package` command is documented as checking "that on-chain bytecode matches a local source tree" [1](#0-0) , but its implementation only diffs self-reported `PackageMetadata` fields and never fetches or compares the actual published module bytecode. This is the same class of bug as the Ammplify report: a value that should be independently derived/propagated (here, cryptographic evidence that on-chain bytecode == claimed source) is instead silently dropped/never checked, and a wrong/incomplete field is trusted downstream, causing users to rely on an incorrect "verified" result.

### Finding Description
`VerifyPackage::execute` builds the package locally, extracts its metadata, then fetches the on-chain registry **without bytecode** (`with_bytecode = false`), and calls `package.verify(&compiled_metadata)`: [2](#0-1) 

`CachedPackageRegistry::verify()` only compares `PackageMetadata` struct fields — `name`, `deps`, `modules` (name/source/source_map metadata, not bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest`: [3](#0-2) 

Critically, `source_digest` is a value the *publisher themselves* supplies as part of `PackageMetadata` at publish time (constructed off-chain from hashing local source files) [4](#0-3) . Nothing in `aptos_framework::code::publish_package` (`aptos-move/framework/aptos-framework/sources/code.move`) or the native publish path (`aptos-move/framework/natives/src/code.rs`) recomputes or checks `source_digest` against the actually-verified/staged module bytecode — it is opaque metadata stored alongside the code, not a chain-enforced binding. `check_upgradability`/`check_dependencies`/`request_publish_with_allowed_deps` only validate module names, upgrade policy ordering, and dependency existence [5](#0-4)  — never bytecode-to-source correspondence.

As a result, `verify-package`'s "verification" reduces to: does the publisher's self-declared metadata match a local rebuild's metadata? It never asks "does the bytecode that was actually loaded/verified into on-chain module storage match what I just compiled?" A malicious or compromised publisher can publish package `P` where the submitted `code: vector<vector<u8>>` bytecode bundle is arbitrary/backdoored while `metadata.source_digest`/`manifest`/`modules` are crafted (or coincidentally/collision-engineered) to equal the hash of an honest source tree that a downstream reviewer would compile locally. `aptos move verify-package` will print `"Successfully verified source of package"` even though the on-chain bytecode is not what was reviewed.

### Impact Explanation
This breaks the core code-safety invariant that "verified bytes, package metadata, dependency declarations, and committed module bytes" must agree. Downstream integrators, auditors, and tooling (e.g., protocol teams verifying a dependency before whitelisting it, or users deciding to trust/interact with a contract because `verify-package` succeeded) receive a false attestation of code integrity for a `compatible`-policy package. This can mask an unauthorized/malicious code replacement at the exact moment users are relying on the CLI's stated guarantee ("checks that on-chain bytecode matches a local source tree"). High impact given verify-package is the primary trust mechanism the CLI documentation offers for reviewing before-interaction; failure here silently defeats code-review-based security assumptions across the ecosystem.

### Likelihood Explanation
High likelihood of triggering unintentionally (miscompiled/mismatched builds going undetected), and straightforward to exploit intentionally: an attacker fully controls both the metadata submitted at publish time and the bytecode bundle, since neither `code.move` nor the native publish path cross-checks `source_digest`/`manifest` against the compiled module bytes. No special permissions beyond normal publish rights are needed; `check_dependencies`'s policy checks and `check_upgradability`'s module-name checks do not touch this attack surface at all [6](#0-5) .

### Recommendation
- In `CachedPackageRegistry::verify` (`aptos-move/cli/src/stored_package.rs`), always fetch on-chain bytecode (`with_bytecode = true` in `VerifyPackage::execute`) and byte-for-byte compare each on-chain module's bytecode against the freshly compiled module bytecode, not just metadata fields.
- Treat a metadata-only match as insufficient for a "Successfully verified" result; require explicit bytecode equality per module before returning success.
- Consider having `aptos_framework::code::publish_package` itself compute/validate `source_digest` server-side against the submitted bundle where feasible, or clearly document that `source_digest`/metadata are unauthenticated, publisher-supplied values that must never be treated as proof of code correspondence by client tooling.

### Proof of Concept
1. Compile and publish a package `P` at address `A` where the actual `code` bytecode bundle contains a backdoored function, but craft/submit `PackageMetadata` (`name`, `deps`, `modules[].source`/`source_map`, `manifest`, `source_digest`) identical to those produced by compiling a known-honest source tree `S` (achievable since these fields are self-supplied bytes, not derived on-chain from the bytecode).
2. A reviewer runs `aptos move verify-package --account A` against local source tree `S`.
3. `VerifyPackage::execute` compiles `S`, calls `CachedPackageRegistry::create(client, A, false)` (bytecode not fetched) [7](#0-6) , then `package.verify(&compiled_metadata)` succeeds because only metadata fields are compared [8](#0-7) .
4. CLI prints `"Successfully verified source of package"` even though the deployed bytecode at `A` differs entirely from `S`.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L256-281)
```text















        assert!(can_change_upgrade_policy_to(old_pack.upgrade_policy, new_pack.upgrade_policy),
            error::invalid_argument(EUPGRADE_WEAKER_POLICY));
        let old_modules = get_module_names(old_pack);

        old_modules.for_each_ref(|old_module| {
            assert!(
                vector::contains(new_modules, old_module),
                EMODULE_MISSING
            );
        });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L297-346)
```text
    /// Check that the upgrade policies of all packages are equal or higher quality than this package. Also
    /// compute the list of module dependencies which are allowed by the package metadata. The later
    /// is passed on to the native layer to verify that bytecode dependencies are actually what is pretended here.
    fun check_dependencies(publish_address: address, pack: &PackageMetadata): vector<AllowedDep>
    acquires PackageRegistry {
        let allowed_module_deps = vector::empty();
        let deps = &pack.deps;
        deps.for_each_ref(|dep| {
            let dep: &PackageDep = dep;
            assert!(exists<PackageRegistry>(dep.account), error::not_found(EPACKAGE_DEP_MISSING));
            if (is_policy_exempted_address(dep.account)) {
                // Allow all modules from this address, by using "" as a wildcard in the AllowedDep
                let account: address = dep.account;
                let module_name = string::utf8(b"");
                vector::push_back(&mut allowed_module_deps, AllowedDep { account, module_name });
            } else {
                let registry = borrow_global<PackageRegistry>(dep.account);
                let found = vector::any(&registry.packages, |dep_pack| {
                    let dep_pack: &PackageMetadata = dep_pack;
                    if (dep_pack.name == dep.package_name) {
                        // Check policy
                        assert!(
                            dep_pack.upgrade_policy.policy >= pack.upgrade_policy.policy,
                            error::invalid_argument(EDEP_WEAKER_POLICY)
                        );
                        if (dep_pack.upgrade_policy == upgrade_policy_arbitrary()) {
                            assert!(
                                dep.account == publish_address,
                                error::invalid_argument(EDEP_ARBITRARY_NOT_SAME_ADDRESS)
                            )
                        };
                        // Add allowed deps
                        let account = dep.account;
                        let k = 0;
                        let r = vector::length(&dep_pack.modules);
                        while (k < r) {
                            let module_name = vector::borrow(&dep_pack.modules, k).name;
                            vector::push_back(&mut allowed_module_deps, AllowedDep { account, module_name });
                            k += 1;
                        };
                        true
                    } else {
                        false
                    }
                });
                assert!(found, error::not_found(EPACKAGE_DEP_MISSING));
            };
        });
        allowed_module_deps
    }
```
