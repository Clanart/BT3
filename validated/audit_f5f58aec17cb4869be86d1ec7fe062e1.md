## Summary

The external report's core lesson is that a **security check that assumes bounded, terminating computation over attacker-influenced state** can be broken when that state is allowed to become degenerate (e.g., a self-referential/cyclic configuration) that the check does not defend against — turning a check meant to gate privileged behavior into either a bypass or an unbounded/broken operation. Searching the Aptos-native publish/ownership analogs, this pattern reproduces in `aptos_framework::object::root_owner`, which is used by the code-publishing and lazy module-initialization flows to determine who ultimately controls a code object, but — unlike its sibling ownership-traversal functions — has no bound on the ownership chain it walks.

### Title
Unbounded object-ownership chain walk in `object::root_owner` used by code-publish/self-init flows enables permanent DoS of upgrade/init authority via a self-referential ownership cycle - (File: `aptos-move/framework/aptos-framework/sources/object.move`)

### Finding Description
`object::root_owner` walks up an object's ownership chain to find the ultimate controlling address: [1](#0-0) 

Unlike this function, the two other ownership-chain traversal functions in the same module, `owns` and `verify_ungated_and_descendant`, explicitly bound the walk with `MAXIMUM_OBJECT_NESTING` and abort if the chain doesn't terminate within that bound: [2](#0-1) [3](#0-2) 

Critically, the module's own test suite demonstrates that a genuine ownership **cycle can be created on-chain**: the first self-transfer of an object to itself succeeds (because `verify_ungated_and_descendant` evaluates the *pre-transfer* owner, which still equals the caller), and only a *second* attempt on the now-cyclic object fails, by hitting the nesting bound: [4](#0-3) 

`root_owner()` is exactly the primitive relied on by the publish-path/code-safety invariant introduced for lazy module initialization:

- `code::publish_package` records, for every module published to an object address, the *transitive root owner* of that object via `root_owner()`, so a later transfer can be detected: [5](#0-4) 

- `init::assert_may_self_initialize`, invoked from `init::internal_maybe_initialize` on every lazily-self-initializing entry-function call, re-derives the object's current root owner with the same unbounded call and compares it to the recorded one to decide whether the module may mint itself a signer: [6](#0-5) [7](#0-6) 

Since the object owning a code deployment is a normal transferable `Object<T>` (the `PackageRegistry` resource has `key`, and `object_code_deployment`'s `ManagingRefs`/`ExtendRef` mechanism does not prevent the object itself from being transferred with the generic `object` module), a party who is (or was) the direct owner of a code object can create a self-referential ownership cycle on it exactly as the existing unit test proves. From that point forward, any call into `root_owner()` on that object — reached both by future `code::publish_package` republish attempts and, more importantly, by every ordinary user transaction that triggers `init::internal_maybe_initialize` in a module hosted at that address — enters the unbounded `while (is_object(obj_owner))` loop and never terminates on its own, running until gas exhaustion.

### Impact Explanation
This breaks a code-safety invariant that the publish/self-init machinery depends on: `root_owner()` is assumed by both `code::publish_package` (object publish/record-owner path) and `init::assert_may_self_initialize` (module-init gate) to be a terminating, well-defined function of on-chain ownership state. Because it lacks the nesting bound its sibling functions enforce, an attacker-reachable ownership configuration (a self-cycle, achievable with an ordinary self-transfer as the module's own tests confirm) turns every future call through this code path into a transaction that always aborts on out-of-gas. For any object-hosted module that uses `LAZY_MODULE_INITIALIZATION` self-init, this permanently bricks that module's initialization/entry-point for all users, and permanently prevents any future compatible upgrade bookkeeping that also depends on `root_owner()` for owner tracking. This is a protocol-level (not network-level) denial-of-service rooted in a missing invariant in mainnet framework code governing code-object ownership and module self-initialization — a high-severity code-safety defect in the publish/init pipeline, since it is caused by, and directly reachable from, the object-owner bookkeeping that gates protected state mutation (signer minting via `create_signer::create_signer(addr)`).

### Likelihood Explanation
The precondition (an object with a self-referential or otherwise cyclic ownership chain) is proven achievable by the framework's own test, `test_cyclic_ownership_transfer_should_fail`, which shows the *first* self-transfer succeeds and only the second one is rejected — i.e., cyclic ownership is not prevented, only detected one step later by bounded-walk functions. Any account that is or was the direct owner of an object hosting a `LAZY_MODULE_INITIALIZATION`-enabled module (e.g., its own `object_code_deployment`-published package, or any object it can move into a self-owned state before or after transferring control) can trigger this. Likelihood is high wherever `LAZY_MODULE_INITIALIZATION` and object-hosted code deployment are combined, though this depends on that feature flag being enabled on mainnet (it is present in the codebase's feature list) — I could not independently verify from the index whether `LAZY_MODULE_INITIALIZATION` is currently active on mainnet or only staged, which affects real-world exploitability today.

### Recommendation
Add the same `MAXIMUM_OBJECT_NESTING`-bounded loop used in `owns` and `verify_ungated_and_descendant` to `object::root_owner`, aborting with `EMAXIMUM_NESTING` (or a dedicated error) if the chain does not terminate within the bound, rather than looping unboundedly. Additionally, consider preventing objects from being transferred to themselves (or into any cycle) at `transfer_raw`/`transfer` time, closing the root cause rather than only capping its consequence.

### Proof of Concept
1. Enable `LAZY_MODULE_INITIALIZATION` (as in `aptos-move/e2e-move-tests/src/tests/init_module_api.rs`, `new_harness()`), and use `object_code_deployment::publish` to deploy a module at a fresh object address that calls `init::internal_maybe_initialize` from an entry function (pattern identical to `OBJECT_MODULE_SRC` in `init_module_api.rs`).
2. As the object's owner, call `object::transfer(owner_signer, code_object, code_object.object_address())` — a self-transfer of the code object to itself. Per `test_cyclic_ownership_transfer_should_fail` in `object.move`, this succeeds and leaves `ObjectCore.owner == code_object_address` (a self-cycle).
3. Call the module's public entry function that invokes `init::internal_maybe_initialize`. `assert_may_self_initialize` calls `object::address_to_object::<ObjectCore>(addr).root_owner()` on the now-cyclic object; because `root_owner()` has no nesting bound, the transaction consumes all gas and aborts, permanently, on every subsequent call to that entry function.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/object.move (L605-635)
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

**File:** aptos-move/framework/aptos-framework/sources/init.move (L54-68)
```text
    public fun internal_maybe_initialize(only_once: bool): Option<signer> {
        assert!(
            features::is_lazy_module_initialization_enabled(),
            error::invalid_state(ELAZY_MODULE_INITIALIZATION_NOT_ENABLED),
        );
        let (addr, module_id) = get_caller_address_and_module_id();
        if (check_and_set_initialized(addr, module_id, only_once)) {
            option::none()
        } else {
            // Guard only when actually minting: a legitimate transfer after initialization must not
            // brick ordinary calls. An abort here rolls back the mark set above.
            assert_may_self_initialize(addr, module_id);
            option::some(create_signer::create_signer(addr))
        }
    }
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
