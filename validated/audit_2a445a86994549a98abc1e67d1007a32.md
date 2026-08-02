## Finding [1](#0-0) 

### Title
`freeze_code_object` only immutabilizes already-published packages, allowing the object owner to still publish brand-new mutable packages to a "frozen" code object address - ([File: aptos-move/framework/aptos-framework/sources/code.move])

### Summary
`code::freeze_code_object` (invoked via `object_code_deployment::freeze_code_object`) is documented and widely relied upon as making a code object's on-chain code permanently immutable. In reality it only iterates the packages that already exist in the `PackageRegistry` at that address and flips each one's `upgrade_policy` field to `immutable`. There is no address-level "frozen" flag. Because the `ExtendRef` used to derive the object's code-signer is preserved forever in `ManagingRefs`, and `object::is_owner` ownership is unaffected by freezing, the object owner can still call `object_code_deployment::upgrade` → `code::publish_package_txn` afterward with a **new package name**. `publish_package`'s conflict check, `check_coexistence`, only rejects module-name collisions with existing packages — it never checks whether the target address (or any existing package there) has been frozen — so a completely new, mutable `PackageMetadata` entry is silently accepted at the "frozen" address.

### Finding Description
`freeze_code_object` sets policy only on the packages present at call time: [2](#0-1) 

`publish_package`'s per-name dispatch either runs `check_upgradability` (which correctly blocks re-publishing under the *same* package name once immutable, via `EUPGRADE_IMMUTABLE`) or, for any *other* package name, only runs `check_coexistence`, which checks module-name uniqueness and nothing about immutability of the address as a whole: [3](#0-2) [4](#0-3) 

`object_code_deployment::upgrade` performs an ownership check but never re-derives or checks whether the code object is supposed to be frozen; it simply regenerates the code-signer from the persisted `ExtendRef` in `ManagingRefs` and republishes: [5](#0-4) 

The module's own doc comments describe freezing as a durable, one-way guarantee ("Once modules are marked as immutable, they cannot be made mutable again"), which strongly implies the whole object/address becomes permanently locked — but the implementation only locks the specific package name(s) that existed at freeze time: [6](#0-5) 

### Impact Explanation
Any party (indexer, explorer, auditor, or downstream contract) that treats a "frozen"/immutable object-code address as guaranteed to never receive new modules can be misled: the owner retains full ability to add a brand-new package (with fresh, mutable/arbitrary upgrade policy) under the same address after "freezing" it, because the `ExtendRef`/ownership capability is never revoked and the coexistence check has no address-level immutability gate. This breaks the code-safety invariant that a frozen code object cannot receive new code, enabling unauthorized/unexpected code addition under an address that downstream consumers assume is permanently fixed — a code-replacement/compatibility-bypass class issue directly in the object code deployment publish path.

### Likelihood Explanation
Likelihood is high for any object-code deployer who deliberately calls `freeze_code_object` intending to lock the address (a documented, encouraged pattern for giving users trust guarantees) and who still holds ownership of the object (the common case, since ownership/`ExtendRef` are not surrendered by freezing). No special privileges beyond being the object owner are required to trigger the gap.

### Recommendation
`freeze_code_object` should record an address/registry-level immutable flag (not just per-package `upgrade_policy`), and `publish_package`/`check_coexistence` should reject *any* new package being added to a registry once that flag (or any existing package's immutable policy, address-wide) is set — not only same-name upgrades. Alternatively, `object_code_deployment::freeze_code_object` should also revoke/burn the `ExtendRef` in `ManagingRefs` so no further signer can be generated for that object, closing off the entire publish path.

### Proof of Concept
1. Owner calls `object_code_deployment::publish` to deploy package `PackageA` (with modules `m1`) to object address `O`.
2. Owner calls `object_code_deployment::freeze_code_object(owner, O)` → `PackageA.upgrade_policy` becomes `immutable`.
3. Owner calls `object_code_deployment::upgrade(owner, metadata_for_PackageB, code_for_PackageB, O)` where `PackageB` has a different name and module set (e.g. `m2`), and an `upgrade_policy` of `compat` or `arbitrary`.
4. In `code::publish_package`, since `PackageB.name != PackageA.name`, only `check_coexistence` runs (no name clash on `m1` vs `m2`), so the call succeeds and `PackageB` (mutable) is added to the registry at `O`.
5. Address `O`, believed to be fully frozen/immutable after step 2, now hosts new mutable code that the owner can continue to upgrade indefinitely.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/code.move (L192-201)
```text
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
```

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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L283-295)
```text
    /// Checks whether a new package with given names can co-exist with old package.
    fun check_coexistence(old_pack: &PackageMetadata, new_modules: &vector<String>) {
        // The modules introduced by each package must not overlap with `names`.
        old_pack.modules.for_each_ref(|old_mod| {
            let old_mod: &ModuleMetadata = old_mod;
            let j = 0;
            while (j < vector::length(new_modules)) {
                let name = vector::borrow(new_modules, j);
                assert!(&old_mod.name != name, error::already_exists(EMODULE_NAME_CLASH));
                j += 1;
            };
        });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L24-30)
```text
/// Freezing modules flow:
/// 1. Assert the `code_object` passed in the function exists in global storage.
/// 2. Assert the `code_object` passed in the function is owned by the `publisher`.
/// 3. Mark all the modules in the `code_object` as immutable.
/// 4. Emits 'Freeze' event with the address of the object with the frozen code.
/// Note: There is no unfreeze function as this gives no benefit if the user can freeze/unfreeze modules at will.
///       Once modules are marked as immutable, they cannot be made mutable again.
```

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L113-133)
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
```
