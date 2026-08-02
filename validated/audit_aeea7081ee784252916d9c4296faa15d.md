## Finding: `aptos move verify-package` never compares actual on-chain bytecode, only self-reported metadata

### Title
`VerifyPackage` (CLI) accepts a package as "verified" without ever checking that on-chain bytecode matches the claimed source — (File: `aptos-move/cli/src/stored_package.rs`)

### Summary
The external report's root cause is that a downstream safety check (`borrowLiquidity`) trusted a derived value without validating it against the real, authoritative constraint (`s.LP_TOKEN_BALANCE`). The Aptos-native analog is `aptos move verify-package`: it is advertised as verifying "that on-chain bytecode matches a local source tree" (`third_party/move/documentation/book/src/cli-deploy.md:12,70-83`), but the actual implementation never fetches or compares the real bytecode — it only compares publisher-supplied `PackageMetadata` fields.

### Finding Description
`VerifyPackage::execute` in [1](#0-0)  builds the package locally, fetches the on-chain registry via `CachedPackageRegistry::create(client, self.account, false)` — passing `with_bytecode = false` — and then calls `package.verify(&compiled_metadata)`.

`CachedPackageRegistry::create` only populates the `bytecode: BTreeMap<String, Vec<u8>>` field when `with_bytecode` is `true` [2](#0-1) . Since `VerifyPackage` passes `false`, no module bytecode is ever downloaded during verification.

`CachedPackageMetadata::verify()` — the function that decides pass/fail — only compares: `name`, `deps`, `modules` (which is `ModuleMetadata { name, source, source_map, extension }`, i.e. the *declared source text and source map*, not bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest` [3](#0-2) . Nowhere does it fetch or hash the actual compiled module bytes stored on-chain and compare them against the bytecode a local build would produce.

On the publish side, `code::publish_package` stores `PackageMetadata` (including publisher-chosen `modules[].source`/`source_digest`) and the raw `code: vector<vector<u8>>` bundle as two independently supplied values [4](#0-3) . Nothing in `publish_package`, `request_publish`, or `request_publish_with_allowed_deps` enforces that the submitted bytecode was actually compiled from the submitted source/source_digest — that binding is only conventionally true when the CLI does the compiling. A publisher who directly crafts a BCS-encoded `PackageMetadata` (as demonstrated by hand-crafted `code::publish_package_txn` calls, e.g. [5](#0-4) ) can pair benign-looking `source`/`source_digest` metadata with arbitrary, unrelated bytecode.

### Impact Explanation
Because `verify-package` only checks metadata equality and never checks bytecode equality, it can report `"Successfully verified source of package"` [6](#0-5)  for a package whose actually-executing bytecode differs entirely from the source it claims to correspond to. This breaks the code-safety invariant the tool exists to provide (documented explicitly as its purpose: "checks that on-chain bytecode matches a local source tree"). Users, auditors, wallets, or other packages that rely on this tool (or the same comparison logic) to establish trust in a dependency's/permissionless package's code before interacting with it, depending on it (via `PackageDep` in `code.move`), or granting it further permissions could be misled into trusting malicious bytecode that was never actually inspected.

### Likelihood Explanation
Likelihood is Medium: exploitation requires a malicious/compromised package publisher to hand-craft a `PackageMetadata` (or use a modified toolchain) that decouples declared source from actual bytecode — this is straightforward since `publish_package_txn` and `object_code_deployment::publish` both take metadata and code as separate, uncorrelated arguments (see e.g. `code::publish_package_txn` usage in `aptos-move/e2e-move-tests/src/tests/code_publishing.data/pack_init_module_code_publish/sources/test.move:13`). Any user, auditor, or automated tool that relies on `aptos move verify-package` for security assurance would then be silently misled.

### Recommendation
- Have `VerifyPackage::execute` call `CachedPackageRegistry::create(client, self.account, true)` to fetch actual on-chain module bytecode.
- Extend `CachedPackageMetadata::verify()` (or add a companion check) to compile the local package, serialize its module bytecode, and byte-for-byte compare it against the bytecode fetched from `registry.get_bytecode(module_name)` for every module — failing verification on any mismatch.
- Document clearly that current metadata-only comparison does not guarantee bytecode provenance, until the fix lands.

### Proof of Concept
1. Compile and publish a package where `PackageMetadata.modules[0].source`/`source_digest` correspond to a trivial, benign module (e.g. `module 0xcafe::m { public fun f() {} }`), but the accompanying `code: vector<vector<u8>>` argument passed to `code::publish_package_txn` is a different, maliciously-crafted module compiled from different source (achievable by hand-assembling the BCS payload as shown in `pack_init_module_code_publish/sources/test.move`).
2. From another machine, run `aptos move verify-package --account <addr>` pointing at a local checkout containing the *benign* source matching the published metadata's `source_digest`.
3. Observe that `VerifyPackage::execute` succeeds and prints `"Successfully verified source of package"`, even though it never downloaded or compared the actual on-chain bytecode (`with_bytecode=false` in `CachedPackageRegistry::create`), which in reality differs from the "verified" source.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L159-231)
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

        // Checks for valid dependencies to other packages
        let allowed_deps = check_dependencies(addr, &pack);

        // Check package against conflicts
        // To avoid prover compiler error on spec
        // the package need to be an immutable variable
        let module_names = get_module_names(&pack);

        // Record, per module in this package, the object's transitive root owner at (re)publish, so
        // lazy self-init can detect a later transfer of the object or an ancestor since that module
        // was published (see `init::internal_maybe_initialize`). Objects only; feature-gated.
        if (features::is_lazy_module_initialization_enabled() && object::is_object(addr)) {
            let owner = object::address_to_object<object::ObjectCore>(addr).root_owner();
            module_names.for_each_ref(|name| {
                init::record_deploy_owner(addr, *name.bytes(), owner);
            });
        };
        let package_immutable = &borrow_global<PackageRegistry>(addr).packages;
        let len = package_immutable.length();
        let index = len;
        let upgrade_number = 0;
        package_immutable.enumerate_ref(|i, old| {
            let old: &PackageMetadata = old;
            if (old.name == pack.name) {
                upgrade_number = old.upgrade_number + 1;
                check_upgradability(old, &pack, &module_names);
                index = i;
            } else {
                check_coexistence(old, &module_names)
            };
        });

        // Assign the upgrade counter.
        pack.upgrade_number = upgrade_number;

        let packages = &mut borrow_global_mut<PackageRegistry>(addr).packages;
        // Update registry
        let policy = pack.upgrade_policy;
        if (index < len) {
            pack.modules.for_each_ref(|m| {
                let m: &ModuleMetadata = m;
                init::reset_initialized(addr, *m.name.bytes());
            });
            *packages.borrow_mut(index) = pack
        } else {
            packages.push_back(pack)
        };

        event::emit(PublishPackage {
            code_address: addr,
            is_upgrade: upgrade_number > 0
        });

        // Request publish
        if (features::code_dependency_check_enabled())
            request_publish_with_allowed_deps(addr, module_names, allowed_deps, code, policy.policy)
        else
        // The new `request_publish_with_allowed_deps` has not yet rolled out, so call downwards
        // compatible code.
            request_publish(addr, module_names, code, policy.policy)
    }
```

**File:** aptos-move/e2e-move-tests/src/tests/code_publishing.data/pack_init_module_code_publish/sources/test.move (L1-15)
```text
module 0xcafe::test {
    use aptos_framework::code;

    fun init_module(s: &signer) {
        // The following metadata and code corresponds to an immutable package called `Package` with compatibility
        // checks. Code:
        //   module 0xcafe::m {
        //       public fun f() {}
        //   }
        let metadata: vector<u8> = vector[7, 80, 97, 99, 107, 97, 103, 101, 1, 0, 0, 0, 0, 0, 0, 0, 0, 64, 68, 56, 49, 69, 55, 68, 70, 69, 70, 54, 51, 52, 66, 50, 56, 56, 49, 69, 48, 48, 51, 69, 67, 70, 49, 54, 66, 54, 66, 69, 53, 53, 66, 69, 57, 49, 54, 54, 55, 53, 65, 65, 66, 66, 50, 67, 57, 52, 70, 55, 56, 52, 54, 67, 56, 70, 57, 55, 68, 49, 50, 57, 54, 65, 107, 31, 139, 8, 0, 0, 0, 0, 0, 2, 255, 37, 138, 203, 9, 128, 48, 16, 68, 239, 91, 133, 164, 0, 177, 1, 123, 240, 30, 68, 214, 236, 32, 193, 124, 150, 68, 5, 187, 55, 65, 230, 52, 239, 61, 171, 236, 78, 62, 176, 82, 226, 136, 97, 30, 204, 242, 3, 67, 15, 74, 245, 57, 117, 54, 141, 109, 134, 110, 61, 10, 11, 54, 205, 193, 187, 183, 11, 151, 163, 242, 229, 247, 208, 122, 203, 34, 5, 181, 162, 174, 68, 86, 160, 72, 130, 228, 124, 255, 3 ... (truncated)
        let code: vector<vector<u8>> = vector[vector[161, 28, 235, 11, 7, 0, 0, 10, 6, 1, 0, 2, 3, 2, 6, 5, 8, 1, 7, 9, 4, 8, 13, 32, 12, 45, 7, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 109, 1, 102, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 202, 254, 0, 1, 0, 0, 0, 1, 2, 0]];

        code::publish_package_txn(s, metadata, code)
    }
}
```
