[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L233-245)
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
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L307-311)
```text
            if (is_policy_exempted_address(dep.account)) {
                // Allow all modules from this address, by using "" as a wildcard in the AllowedDep
                let account: address = dep.account;
                let module_name = string::utf8(b"");
                vector::push_back(&mut allowed_module_deps, AllowedDep { account, module_name });
```

**File:** aptos-move/framework/natives/src/code.rs (L236-238)
```rust
    /// Allowed module dependencies. Empty for no restrictions. An empty string in the set
    /// allows all modules from that address.
    pub allowed_deps: Option<BTreeMap<AccountAddress, BTreeSet<String>>>,
```
