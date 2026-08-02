## Analysis

The external report's root invariant is: **a verification/authorization function silently diverges from the real on-chain state it claims to check, letting a privileged action proceed on stale or wrong data.** I mapped this onto Aptos's publish-verification path rather than reusing the Solidity bug directly.

I traced 3 candidate paths:
1. `code::check_dependencies` policy-exemption bypass — held up under review (properly gated, only trusted addresses are exempt) [1](#0-0) .
2. `object_code_deployment::upgrade`/`freeze_code_object` ownership checks — both correctly gate on `object::is_owner` before mutating `ManagingRefs`/`PackageRegistry` [2](#0-1) . Discarded, no gap found.
3. **CLI `verify-package` bytecode/metadata mismatch** — this is the strongest candidate, detailed below.

### Title
`aptos move verify-package` never compares on-chain module bytecode to source, allowing verified-but-mismatched code - (File: `aptos-move/cli/src/commands.rs`, `aptos-move/cli/src/stored_package.rs`)

### Summary
The Aptos CLI's `VerifyPackage` command is the tool users/auditors rely on to confirm that bytecode published on-chain actually corresponds to a given source tree. It fetches the on-chain `PackageRegistry` with `with_bytecode: false`, so the real `.mv` bytecode is never downloaded, and `CachedPackageMetadata::verify()` only diffs self-reported metadata fields (`name`, `deps`, `modules` (i.e., the publisher-supplied source text/source-map blobs), `manifest`, `upgrade_policy`, `extension`, `source_digest`) — never the executable bytecode itself.

### Finding Description
In `VerifyPackage::execute`, the registry is created with `with_bytecode = false`: [3](#0-2) 

`CachedPackageRegistry::create` only populates `self.bytecode` when `with_bytecode` is `true`; here it stays empty: [4](#0-3) 

`CachedPackageMetadata::verify()` then only compares metadata struct fields — `name`, `deps`, `modules`, `manifest`, `upgrade_policy`, `extension`, `source_digest` — none of which is the compiled bytecode delivered in the `code: vector<vector<u8>>` argument to `code::publish_package_txn`: [5](#0-4) 

Critically, `ModuleMetadata.source` (the field compared inside `modules`) is arbitrary, publisher-supplied "documentation" data with no on-chain link to the actual bytecode: `code::publish_package` stores `pack: PackageMetadata` and `code: vector<vector<u8>>` as two independent parameters, and no native or Move-level check enforces that `code[i]` was compiled from `pack.modules[i].source`: [6](#0-5) 

So a publisher can submit honest-looking source metadata (matching `source_digest`) alongside bytecode compiled from a different, malicious source. Anyone running `aptos move verify-package` against that package will see `"Successfully verified source of package"` even though the deployed executable code was never actually checked against that source.

### Impact Explanation
`verify-package` is documented as the canonical mechanism for "code review and reproducibility" to confirm "on-chain bytecode matches a local source tree" [7](#0-6) . Because the actual bytecode bytes are never fetched or diffed, this guarantee is false: it verifies self-reported metadata text, not the code that executes. Downstream integrators, auditors, or governance processes that rely on this command to confirm parity between claimed source and deployed bytecode can be misled into trusting malicious modules as "verified," directly matching the required impact class "Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes."

### Likelihood Explanation
This triggers on every invocation of `aptos move verify-package` against any package where the publisher intentionally (or accidentally via toolchain divergence) submits bytecode that doesn't match the reported source metadata — no special privilege is needed by the publisher beyond normal package publish rights, and no interaction from the module owner is required for the false verification result to occur.

### Recommendation
Have `VerifyPackage` always call `CachedPackageRegistry::create(..., with_bytecode = true)` and extend `CachedPackageMetadata::verify` (or a new step in `commands.rs`) to fetch each module's on-chain bytecode via `get_bytecode` and byte-compare it against `pack.extract_code()` (the bytecode produced by rebuilding the local source), rather than only diffing metadata structs.

### Proof of Concept
1. Publish a package where `metadata_serialized`'s `modules[i].source` is the innocuous, real source of `module.move`, and `source_digest` is computed to match, but the accompanying `code[i]` bytecode blob is a different, malicious compiled module (Move's bytecode verifier/loader only checks structural/type safety, not source correspondence).
2. Run `aptos move verify-package --account <addr>` against the honest local source tree.
3. Observe `Successfully verified source of package` is printed, per `aptos-move/cli/src/commands.rs:2139`, even though the deployed bytecode differs from the verified source, because bytecode was never fetched (`with_bytecode=false`) nor compared in `verify()`.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/code.move (L157-231)
```text
    /// Publishes a package at the given signer's address. The caller must provide package metadata describing the
    /// package.
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L300-327)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L113-141)
```text
    public entry fun upgrade(
        publisher: &signer,
        metadata_serialized: vector<u8>,
        code: vector<vector<u8>>,
        code_object: Object<PackageRegistry>,
    ) {
        let publisher_address = signer::address_of(publisher);
        assert!(
            object::is_owner(code_object, publisher_address),
            error::permission_denied(ENOT_CODE_OBJECT_OWNER),
        );

        let code_object_address = code_object.object_address();
        assert!(exists<ManagingRefs>(code_object_address), error::not_found(ECODE_OBJECT_DOES_NOT_EXIST));

        let extend_ref = &borrow_global<ManagingRefs>(code_object_address).extend_ref;
        let code_signer = &extend_ref.generate_signer_for_extending();
        code::publish_package_txn(code_signer, metadata_serialized, code);

        event::emit(Upgrade { object_address: signer::address_of(code_signer), });
    }

    /// Make an existing upgradable package immutable. Once this is called, the package cannot be made upgradable again.
    /// Each `code_object` should only have one package, as one package is deployed per object in this module.
    /// Requires the `publisher` to be the owner of the `code_object`.
    public entry fun freeze_code_object(publisher: &signer, code_object: Object<PackageRegistry>) {
        code::freeze_code_object(publisher, code_object);

        event::emit(Freeze { object_address: code_object.object_address(), });
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

**File:** aptos-move/cli/src/stored_package.rs (L42-66)
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

**File:** third_party/move/documentation/book/src/cli-deploy.md (L1-12)
```markdown
# Publish

Subcommands that publish a Move package to a network. All of them accept the [shared package options](./cli.md#package-options) and [shared transaction options](./cli.md#transaction-options); only the command-specific flags are listed below.

## Picking a publication pattern

Two patterns are supported side by side; pick based on how you want the package's address to behave:

- **Account publishing** (`publish` / `deploy`): the modules live at the **signer's account address**. Simplest pattern. Upgrades happen by re-publishing from the same account. The signer owns upgrade authority and can't transfer it.
- **Code-object publishing** (`deploy-object` / `upgrade-object`): the modules live at a **separate, derived object address**. Upgrade authority is held in a code object and can be transferred. Use this when modules need an address independent of any single account.

For code review and reproducibility, [`verify-package`](#aptos-move-verify-package) checks that on-chain bytecode matches a local source tree.
```
