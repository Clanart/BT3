## Finding: Unbounded ownership-cycle traversal in `object::root_owner` used by the code-publish path can permanently brick package upgrades

The external Notional report's bug class is "an unhandled edge case in an arithmetic/traversal operation on a critical financial/state-mutation path causes the whole operation to revert, and that path can be reused elsewhere and disrupt operations." The Aptos-native analog I found is in the object ownership model used by the code-publishing flow.

### Title
Unbounded cyclic-ownership loop in `object::root_owner` reachable from `code::publish_package` permanently blocks package upgrades - (File: `aptos-move/framework/aptos-framework/sources/object.move`)

### Summary
`object::root_owner` walks the object ownership chain without the nesting bound that every sibling traversal function (`owns`, `verify_ungated_and_descendant`) enforces via `MAXIMUM_OBJECT_NESTING`. Since Aptos objects can legitimately form ownership cycles (the framework's own tests demonstrate a self-owning object can be created), calling `root_owner()` on an object whose ownership chain contains a cycle loops until gas is exhausted. This function is invoked from `code::publish_package` on every republish/upgrade of an object-hosted package when `LAZY_MODULE_INITIALIZATION` is enabled, so a cyclic ownership chain permanently prevents any future upgrade (or initial lazy-init record) for that code object.

### Finding Description
`object::root_owner` is defined with no loop-termination guard: [1](#0-0) 

Contrast this with the two other ownership-chain traversals in the same module, both of which explicitly bound iteration count to guard against cycles: [2](#0-1) [3](#0-2) 

The framework's own tests prove a cycle is directly achievable: a self-transfer of an object to itself succeeds on the first call (because the destination's existing owner already equals the caller, so `verify_ungated_and_descendant`'s loop is never entered), after which the object is owned by itself: [4](#0-3) 

`root_owner` is called directly from the code publishing path, specifically to compute the "deploy owner" recorded for lazy module self-initialization on every (re)publish to an object address: [5](#0-4) 

`code::publish_package` (and hence `object_code_deployment::publish`/`upgrade`, and the `large_packages` chunked-publish entry points, all of which funnel into it) is the canonical Aptos "publish/upgrade" entry point, so any transaction that touches this function on a cyclically-owned object address will hit the unbounded loop.

### Impact Explanation
Once an object used to host published code (via `object_code_deployment`) has a cyclic ownership chain in its `owner` field (self-loop, or a longer cycle formed by legitimate nested `transfer` calls that stay within the bounded-nesting check), any subsequent call to `code::publish_package` for that address — i.e. any future package upgrade — will invoke `object::root_owner` and loop until the transaction runs out of gas, aborting every time. This makes the package permanently unable to be upgraded from that point forward whenever `FeatureFlag::LAZY_MODULE_INITIALIZATION` is enabled, an effect equivalent to an implicit, unauthorized "freeze" of the code object that bypasses the deliberate, ownership-checked `code::freeze_code_object` / `object_code_deployment::freeze_code_object` entry points: [6](#0-5) [7](#0-6) 

This is squarely a publish-path/code-safety issue: it corrupts the invariant that code-object upgrade/freeze authority is governed only by explicit, checked operations, and instead lets an incidental (or adversarially engineered) ownership-graph state silently and irreversibly disable upgrade capability.

### Likelihood Explanation
Forming a cycle requires only ordinary, permitted `transfer`/`transfer_to_object` calls by the object's legitimate owner (as the framework's own passing test `test_cyclic_ownership_transfer_should_fail` shows the *first* self-transfer succeeds unchecked). Any workflow that programmatically manages nested object ownership (marketplaces, vaults, staking pools, or simple user error/tooling bugs) that ends up re-parenting an object into one of its own descendants can trigger this, and once triggered the effect is permanent and only surfaces the next time someone tries to upgrade the package — a code-object owner may not even realize the cycle exists until an upgrade unexpectedly and permanently fails.

### Recommendation
Add the same `MAXIMUM_OBJECT_NESTING` bound (and abort with `EMAXIMUM_NESTING`) used in `owns` and `verify_ungated_and_descendant` to `object::root_owner`, so a cyclic or overly deep ownership chain results in a clean, well-defined abort instead of a silent unbounded loop that can permanently disable code-object upgrade capability.

### Proof of Concept
1. Enable `FeatureFlag::LAZY_MODULE_INITIALIZATION`.
2. Publish a package to an object via `object_code_deployment::publish` (creates `code_object` owned by `publisher`).
3. As the object owner, call `object::transfer(&owner_signer, code_object_as_ObjectCore, code_object_address)` — a self-transfer. Per `test_cyclic_ownership_transfer_should_fail`'s first call, this succeeds and leaves `code_object`'s `ObjectCore.owner == code_object_address` (self-loop).
4. Attempt `object_code_deployment::upgrade` (or `code::publish_package_txn`) on `code_object`. Because `is_object(addr)` is true and the feature is enabled, `code::publish_package` executes `object::address_to_object::<ObjectCore>(addr).root_owner()`, which loops `while (is_object(obj_owner)) { obj_owner = ...owner() }` forever on the self-owned address, exhausting gas and aborting every subsequent upgrade attempt permanently.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/object.move (L605-639)
```text
    /// This checks that the destination address is eventually owned by the owner and that each
    /// object between the two allows for ungated transfers. Note, this is limited to a depth of 8
    /// objects may have cyclic dependencies.
    fun verify_ungated_and_descendant(owner: address, destination: address) {
        let current_address = destination;
        assert!(
            exists<ObjectCore>(current_address),
            error::not_found(EOBJECT_DOES_NOT_EXIST),
        );

        let object = borrow_global<ObjectCore>(current_address);
        assert!(
            object.allow_ungated_transfer,
            error::permission_denied(ENO_UNGATED_TRANSFERS),
        );

        let current_address = object.owner;
        let count = 0;
        while (owner != current_address) {
            count += 1;
            assert!(count < MAXIMUM_OBJECT_NESTING, error::out_of_range(EMAXIMUM_NESTING));
            // At this point, the first object exists and so the more likely case is that the
            // object's owner is not an object. So we return a more sensible error.
            assert!(
                exists<ObjectCore>(current_address),
                error::permission_denied(ENOT_OBJECT_OWNER),
            );
            let object = borrow_global<ObjectCore>(current_address);
            assert!(
                object.allow_ungated_transfer,
                error::permission_denied(ENO_UNGATED_TRANSFERS),
            );
            current_address = object.owner;
        };
    }
```

**File:** aptos-move/framework/aptos-framework/sources/object.move (L706-737)
```text
    #[view]
    /// Return true if the provided address has indirect or direct ownership of the provided object.
    ///
    /// Note: intentionally not using `self` as first argument, as a.owns(b) syntax would be ambiguous.
    public fun owns<T: key>(object: Object<T>, owner: address): bool {
        let current_address = object.object_address();

        assert!(
            exists<ObjectCore>(current_address),
            error::not_found(EOBJECT_DOES_NOT_EXIST),
        );

        if (current_address == owner) {
            return true
        };

        let object = borrow_global<ObjectCore>(current_address);
        let current_address = object.owner;

        let count = 0;
        while (owner != current_address) {
            count += 1;
            assert!(count < MAXIMUM_OBJECT_NESTING, error::out_of_range(EMAXIMUM_NESTING));
            if (!exists<ObjectCore>(current_address)) {
                return false
            };

            let object = borrow_global<ObjectCore>(current_address);
            current_address = object.owner;
        };
        true
    }
```

**File:** aptos-move/framework/aptos-framework/sources/object.move (L739-748)
```text
    #[view]
    /// Returns the root owner of an object. As objects support nested ownership, it can be useful
    /// to determine the identity of the starting point of ownership.
    public fun root_owner<T: key>(self: Object<T>): address {
        let obj_owner = self.owner();
        while (is_object(obj_owner)) {
            obj_owner = address_to_object<ObjectCore>(obj_owner).owner();
        };
        obj_owner
    }
```

**File:** aptos-move/framework/aptos-framework/sources/object.move (L1081-1099)
```text
    #[test(creator = @0x123)]
    #[expected_failure(abort_code = 131078, location = Self)]
    fun test_cyclic_ownership_transfer_should_fail(creator: &signer) {
        let obj1 = create_simple_object(creator, b"1");
        // This creates a cycle (self-loop) in ownership.
        transfer(creator, obj1, obj1.object_address());
        // This should fails as the ownership is cyclic.
        transfer(creator, obj1, obj1.object_address());
    }

    #[test(creator = @0x123)]
    #[expected_failure(abort_code = 131078, location = Self)]
    fun test_cyclic_ownership_owns_should_fail(creator: &signer) {
        let obj1 = create_simple_object(creator, b"1");
        // This creates a cycle (self-loop) in ownership.
        transfer(creator, obj1, obj1.object_address());
        // This should fails as the ownership is cyclic.
        let _ = owns(obj1, signer::address_of(creator));
    }
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L179-187)
```text
        // Record, per module in this package, the object's transitive root owner at (re)publish, so
        // lazy self-init can detect a later transfer of the object or an ancestor since that module
        // was published (see `init::internal_maybe_initialize`). Objects only; feature-gated.
        if (features::is_lazy_module_initialization_enabled() && object::is_object(addr)) {
            let owner = object::address_to_object<object::ObjectCore>(addr).root_owner();
            module_names.for_each_ref(|name| {
                init::record_deploy_owner(addr, *name.bytes(), owner);
            });
        };
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

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L135-142)
```text
    /// Make an existing upgradable package immutable. Once this is called, the package cannot be made upgradable again.
    /// Each `code_object` should only have one package, as one package is deployed per object in this module.
    /// Requires the `publisher` to be the owner of the `code_object`.
    public entry fun freeze_code_object(publisher: &signer, code_object: Object<PackageRegistry>) {
        code::freeze_code_object(publisher, code_object);

        event::emit(Freeze { object_address: code_object.object_address(), });
    }
```
