## Finding: Unbounded ownership-cycle traversal in `object::root_owner()` can permanently brick code-object upgrade / lazy self-init

### Title
`object::root_owner()` lacks the nesting-depth bound enforced by `owns()`/`verify_ungated_and_descendant()`, enabling an unrecoverable denial of code-object upgrade and lazy self-init - (File: `aptos-move/framework/aptos-framework/sources/object.move`)

### Summary
`code::publish_package` (used by both direct account publish and `object_code_deployment::upgrade`) and `init::assert_may_self_initialize` (the gate for `init::internal_maybe_initialize`, the new lazy module-initialization entry point) both compute an object's `root_owner()` to authorize state mutation. Unlike the sibling functions `object::owns()` and `object::verify_ungated_and_descendant()`, which explicitly bound their ownership-chain walk to `MAXIMUM_OBJECT_NESTING` (8), `root_owner()` has no such bound. If an object is ever made to own itself (a 1-object ownership cycle), every future call to `root_owner()` on that address loops until the transaction runs out of gas, permanently blocking any code path that depends on it.

### Finding Description
`object.move` defines a bounded ownership walk pattern used everywhere else in the module: [1](#0-0) 

and the read-only variant `owns()`: [2](#0-1) 

Both loops assert `count < MAXIMUM_OBJECT_NESTING` on every hop. But `root_owner()` has no such guard: [3](#0-2) 

A 1-object ownership cycle (an object owning itself) is directly creatable through the normal `transfer` path. The module's own regression test demonstrates this: the *first* self-transfer of an object to its own address succeeds (because `verify_ungated_and_descendant` starts its walk at `object.owner`, which still equals the transferring `owner` at that point, so the loop body never executes), and only the *second* self-transfer aborts on `EMAXIMUM_NESTING`: [4](#0-3) 

Once an object address is self-owned this way, `root_owner()` never terminates on its own: `obj_owner` keeps resolving to the same self-owned address, and `is_object(obj_owner)` stays `true` forever, so the loop only stops when the transaction exhausts its gas budget.

This directly affects the publish path. `code::publish_package` calls `root_owner()` on the target address whenever lazy module initialization is enabled and the address is an object, to record/refresh the deploy-owner used to gate self-init: [5](#0-4) 

And `init::assert_may_self_initialize`, the gate that every call to `init::internal_maybe_initialize` (the lazy self-init entry point) goes through, does the same: [6](#0-5) 

If the code object backing a package is ever self-owned, both `object_code_deployment::upgrade` (via `code::publish_package_txn` → `publish_package`) and any lazily self-initializing module hosted at that address will unconditionally exhaust gas and abort, forever, on every future invocation.

### Impact Explanation
This is a code-safety invariant break in the publish/upgrade and code-ownership machinery: the same "root owner" computation used to authorize object-hosted code upgrades and lazy module self-init (`init::internal_maybe_initialize`) can be forced into an unbounded loop, causing:
- Permanent denial of the ability to upgrade a code object once its backing object is (even accidentally, or via an exposed `TransferRef`/`LinearTransferRef` used by a marketplace/automation contract) transferred to itself.
- Permanent denial of lazy module self-initialization for any module relying on `init::internal_maybe_initialize` at that address, since `assert_may_self_initialize` will always abort on gas exhaustion instead of returning a deterministic `EOWNER_CHANGED_SINCE_DEPLOY` error.

This is effectively an unintended, irreversible "freeze" reachable without going through the sanctioned `code::freeze_code_object`/owner-authorization path, and it directly touches the code-object ownership and upgrade-authority invariants the Publish Impact Gate calls out.

### Likelihood Explanation
Creating the self-owned cycle requires a `transfer` call where destination equals the object's own current address. This is directly reachable by the object's own owner in a single call (as shown by the existing unit test), and also by anyone holding a `TransferRef`/`LinearTransferRef` for that object (e.g., automation, escrow, or marketplace contracts that transfer objects on behalf of users) if the destination address can be influenced to equal the object's own address. The precondition is narrow (the owner or a ref-holder must make this specific transfer), so likelihood is low-to-moderate, but there is no existing safeguard preventing it, and the resulting DoS is silent and permanent.

### Recommendation
Add the same `MAXIMUM_OBJECT_NESTING` bound to `root_owner()` that already exists in `owns()` and `verify_ungated_and_descendant()`, aborting with `EMAXIMUM_NESTING` (or a dedicated error) instead of looping unboundedly. Additionally, consider rejecting self-transfers (`to == object`) in `transfer_raw`/`transfer` outright, since a 1-object ownership cycle has no legitimate use and is inconsistent with the nesting-cycle protections the module otherwise enforces.

### Proof of Concept
1. Enable `FeatureFlag::LAZY_MODULE_INITIALIZATION` and `OBJECT_CODE_DEPLOYMENT`.
2. Deploy a package to an object via `object_code_deployment::publish` (owner = `@0xcafe`), object address = `obj`.
3. As `@0xcafe`, call `object::transfer(owner, code_object, obj)` — i.e., transfer the object to its own address. This succeeds per the loop-entry behavior shown in `test_cyclic_ownership_transfer_should_fail`'s first call.
4. Call `object_code_deployment::upgrade` (or any entry function in the deployed module that calls `init::internal_maybe_initialize`). Internally this reaches `object::address_to_object::<ObjectCore>(obj).root_owner()`, which loops indefinitely (`is_object(obj_owner)` always true) until the transaction aborts on out-of-gas — permanently preventing further upgrades or lazy self-init at `obj`.

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

**File:** aptos-move/framework/aptos-framework/sources/object.move (L721-737)
```text

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

**File:** aptos-move/framework/aptos-framework/sources/object.move (L1081-1089)
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

**File:** aptos-move/framework/aptos-framework/sources/init.move (L70-83)
```text
    /// Aborts unless the module at `addr` may self-initialize now. Only object-hosted modules are
    /// gated: an object must still have the transitive root owner recorded for this module at
    /// publish, so a transfer of the object or an ancestor, or its deletion, blocks self-init; an
    /// object with no record is fail-closed. Account addresses authorize their own code by publishing.
    fun assert_may_self_initialize(addr: address, module_id: ModuleId) {
        let recorded = recorded_deploy_owner(addr, module_id);
        let ok = if (recorded.is_some()) {
            object::is_object(addr)
                && recorded.destroy_some() == object::address_to_object<ObjectCore>(addr).root_owner()
        } else {
            !object::is_object(addr)
        };
        assert!(ok, error::permission_denied(EOWNER_CHANGED_SINCE_DEPLOY));
    }
```
