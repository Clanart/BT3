[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/code.move (L233-254)
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
    }
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L300-330)
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
                        // Add allowed deps
                        let account = dep.account;
                        let k = 0;
```

**File:** aptos-move/framework/natives/src/code.rs (L336-357)
```rust

    // Add own modules to allowed deps
    let allowed_deps = allowed_deps.map(|mut allowed| {
        allowed
            .entry(destination)
            .or_default()
            .extend(expected_modules.clone());
        allowed
    });

    let code_context = context.extensions_mut().get_mut::<NativeCodeContext>();
    if code_context.requested_module_bundle.is_some() || !code_context.enabled {
        // Can't request second time or if publish requests are not allowed.
        return Err(SafeNativeError::abort(EALREADY_REQUESTED));
    }
    code_context.requested_module_bundle = Some(PublishRequest {
        destination,
        bundle: ModuleBundle::new(code),
        expected_modules,
        allowed_deps,
        check_compat: policy != ARBITRARY_POLICY,
    });
```
