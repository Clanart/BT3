[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/code.move (L159-164)
```text
    public fun publish_package(owner: &signer, pack: PackageMetadata, code: vector<vector<u8>>) acquires PackageRegistry {
        // Disallow incompatible upgrade mode. Governance can decide later if this should be reconsidered.
        assert!(
            pack.upgrade_policy.policy > upgrade_policy_arbitrary().policy,
            error::invalid_argument(EINCOMPATIBLE_POLICY_DISABLED),
        );
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L233-253)
```text
    public fun freeze_code_object(publisher: &signer, code_object: Object<PackageRegistry>) acquires PackageRegistry {
        let code_object_addr = code_object.object_address();
        assert!(exists<PackageRegistry>(code_object_addr), error::not_found(ECODE_OBJECT_DOES_NOT_EXIST));
        assert!(
            object::is_owner(code_object, signer::address_of(publisher)),
            error::permission_denied(ENOT_PACKAGE_OWNER)
        );

        let registry = borrow_global_mut<PackageRegistry>(code_object_addr);
        registry.packages.for_each_mut(|pack| {
            let package: &mut PackageMetadata = pack;
            package.upgrade_policy = upgrade_policy_immutable();
        });

        // We unfortunately have to make a copy of each package to avoid borrow checker issues as check_dependencies
        // needs to borrow PackageRegistry from the dependency packages.
        // This would increase the amount of gas used, but this is a rare operation and it's rare to have many packages
        // in a single code object.
        registry.packages.for_each(|pack| {
            check_dependencies(code_object_addr, &pack);
        });
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L266-281)
```text
    /// Checks whether the given package is upgradable, and returns true if a compatibility check is needed.
    fun check_upgradability(
        old_pack: &PackageMetadata, new_pack: &PackageMetadata, new_modules: &vector<String>) {
        assert!(old_pack.upgrade_policy.policy < upgrade_policy_immutable().policy,
            error::invalid_argument(EUPGRADE_IMMUTABLE));
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

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L16-30)
```text
/// Upgrading modules flow:
/// 1. Assert the `code_object` passed in the function is owned by the `publisher`.
/// 2. Assert the `code_object` passed in the function exists in global storage.
/// 2. Retrieve the `ExtendRef` from the `code_object` and generate the signer from this.
/// 3. Upgrade the module with the `metadata_serialized` and `code` passed in the function.
/// 4. Emits 'Upgrade' event with the address of the object with the upgraded code.
/// Note: If the modules were deployed as immutable when calling `publish`, the upgrade will fail.
///
/// Freezing modules flow:
/// 1. Assert the `code_object` passed in the function exists in global storage.
/// 2. Assert the `code_object` passed in the function is owned by the `publisher`.
/// 3. Mark all the modules in the `code_object` as immutable.
/// 4. Emits 'Freeze' event with the address of the object with the frozen code.
/// Note: There is no unfreeze function as this gives no benefit if the user can freeze/unfreeze modules at will.
///       Once modules are marked as immutable, they cannot be made mutable again.
```

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L42-47)
```text
    /// Object code deployment feature not supported.
    const EOBJECT_CODE_DEPLOYMENT_NOT_SUPPORTED: u64 = 1;
    /// Not the owner of the `code_object`
    const ENOT_CODE_OBJECT_OWNER: u64 = 2;
    /// `code_object` does not exist.
    const ECODE_OBJECT_DOES_NOT_EXIST: u64 = 3;
```

**File:** aptos-move/framework/aptos-experimental/sources/large_packages.move (L113-129)
```text
    public entry fun stage_code_chunk_and_upgrade_object_code(
        owner: &signer,
        metadata_chunk: vector<u8>,
        code_indices: vector<u16>,
        code_chunks: vector<vector<u8>>,
        code_object: Object<PackageRegistry>
    ) acquires StagingArea {
        let staging_area =
            stage_code_chunk_internal(
                owner,
                metadata_chunk,
                code_indices,
                code_chunks
            );
        upgrade_object_code(owner, staging_area, code_object);
        cleanup_staging_area(owner);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/code.spec.move (L1-8)
```text
spec aptos_framework::code {
    /// <high-level-req>
    /// No.: 1
    /// Requirement: Updating a package should fail if the user is not the owner of it.
    /// Criticality: Critical
    /// Implementation: The publish_package function may only be able to update the package if the signer is the actual
    /// owner of the package.
    /// Enforcement: The Aptos upgrade native functions have been manually audited.
```

**File:** aptos-move/framework/aptos-framework/sources/code.spec.move (L107-116)
```text
    spec freeze_code_object(publisher: &signer, code_object: Object<PackageRegistry>) {
        pragma aborts_if_is_partial;

        let code_object_addr = code_object.inner;
        aborts_if !exists<object::ObjectCore>(code_object_addr);
        aborts_if !exists<PackageRegistry>(code_object_addr);
        aborts_if !object::is_owner(code_object, signer::address_of(publisher));

        modifies global<PackageRegistry>(code_object_addr);
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
