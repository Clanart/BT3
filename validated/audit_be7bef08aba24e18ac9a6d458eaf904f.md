## Analysis

The external report's bug class is: **a critical operation trusts a derived value without validating that value stays consistent / bounded**, allowing a "stale"/corrupted read to reach a protected code path. The closest Aptos-native analog is not in the price-oracle sense, but in the **object ownership resolution used to gate lazy module self-initialization and object-based code publish/upgrade** — `object::root_owner()`.

### Root cause

`object::root_owner()` walks the ownership chain of an object with **no depth bound**: [1](#0-0) 

This is inconsistent with every other ownership-chain traversal in the same module, which explicitly caps nesting at `MAXIMUM_OBJECT_NESTING = 8` to guard against cycles: [2](#0-1) [3](#0-2) 

Critically, `verify_ungated_and_descendant` (invoked by `transfer`/`transfer_raw`) only verifies that the **pre-transfer** chain from `to` reaches back to the signer's own address within 8 hops — it never checks that `to` is not already a descendant of `object` being moved. This allows a legitimate owner, using two ordinary, individually-valid transfers, to construct a genuine ownership **cycle** (e.g. `O1.owner = O2` and `O2.owner = O1`), detached from any account root: [4](#0-3) 

Once such a cycle exists, any call into `root_owner()` on either object loops until gas is exhausted. This function is exactly what gates:

1. Object-based code publish/upgrade's deploy-owner recording in `code::publish_package`: [5](#0-4) 

2. The self-init ownership guard in `init::assert_may_self_initialize`, which is the mechanism that authorizes minting a signer for lazily-initializing an object-hosted module: [6](#0-5) 

### Impact

Any object address whose ownership chain becomes cyclic can never again successfully:
- Publish or upgrade a package to it while `LAZY_MODULE_INITIALIZATION` is enabled (since `code::publish_package` calls `root_owner()` for object addresses), and
- Complete `init::internal_maybe_initialize` for any module hosted there (since `assert_may_self_initialize` calls `root_owner()`).

Every transaction touching these paths will run until out-of-gas, permanently bricking the object's ability to publish/upgrade code or self-initialize — a code-safety invariant violation directly in the object-code-deployment/publish path, triggerable by an object owner performing only legitimate, individually-valid `object::transfer` calls (no special privilege required beyond normal object ownership).

### Title
Unbounded `object::root_owner()` traversal allows self-inflicted ownership cycles to permanently brick object code publish/upgrade and lazy module self-init - (File: `aptos-move/framework/aptos-framework/sources/object.move`)

### Summary
`object::root_owner()` lacks the `MAXIMUM_OBJECT_NESTING` bound that all sibling ownership-chain traversals (`owns`, `verify_ungated_and_descendant`) enforce. Because `object::transfer` only validates the pre-transfer chain from the destination back to the signer and never checks that the destination is not already a descendant of the object being moved, a normal object owner can perform two ordinary transfers to create a genuine ownership cycle. `root_owner()` is used by `code::publish_package` (object deploy-owner recording) and by `init::assert_may_self_initialize` (lazy module self-init gate); once a cycle exists, both paths loop until gas exhaustion.

### Finding Description
- `code::publish_package` calls `object::address_to_object<ObjectCore>(addr).root_owner()` whenever `features::is_lazy_module_initialization_enabled()` and `addr` is an object, to record the deploy owner for later self-init gating: [5](#0-4) 
- `init::assert_may_self_initialize` similarly calls `root_owner()` to compare the recorded deploy owner to the current root owner before minting a self-init signer: [6](#0-5) 
- `root_owner()` has no cycle/depth protection: [7](#0-6) 
- `verify_ungated_and_descendant`, which gates `transfer`, only checks that walking up from the *destination* reaches the *transferring signer's own address* within 8 hops — it does not check whether `destination` is already owned (directly or transitively) by `object` (the item being moved): [2](#0-1) 

### Impact Explanation
Any object address that becomes part of an ownership cycle can never again complete a `code::publish_package` transaction (blocking `object_code_deployment::publish`/`upgrade`) or `init::internal_maybe_initialize` while `LAZY_MODULE_INITIALIZATION` is enabled, because both call `root_owner()`, which loops indefinitely on a cycle and can only terminate via gas exhaustion. This is a permanent denial of the object-code publish/upgrade and module-init state-mutation paths for the affected address — a protected-state-mutation reachability failure directly in the publish path, matching "Verifier, module-init, native validation, or write-set-publish failure that reaches protected state mutation."

### Likelihood Explanation
The cycle can be created with two ordinary `object::transfer` calls executed by the legitimate owner (or anyone the owner delegates transfer rights to via nested object ownership), using only default `allow_ungated_transfer = true` objects. No privileged role, governance action, or bytecode manipulation is required — only standard object semantics already exposed on mainnet. The only precondition is that `LAZY_MODULE_INITIALIZATION` be enabled (a feature flag actively present in the framework and exercised by `object_code_deployment`/`code::publish_package`).

### Recommendation
Bound `root_owner()`'s traversal with the same `MAXIMUM_OBJECT_NESTING` check used in `owns` and `verify_ungated_and_descendant`, aborting with `EMAXIMUM_NESTING` if exceeded, and/or extend `verify_ungated_and_descendant` to reject transfers where `to` is already a (transitive) owner of `object`, preventing cycle formation at the source.

### Proof of Concept
1. Account `A` creates object `O1` (owner = `A`, `allow_ungated_transfer = true`).
2. Account `A` creates object `O2` (owner = `A`, `allow_ungated_transfer = true`), then calls `object::transfer(A, O2, O1)`. `verify_ungated_and_descendant(A, O1)` succeeds trivially (O1's owner is already `A`), so `O2.owner` becomes `O1`.
3. Account `A` calls `object::transfer(A, O1, O2)`. `verify_ungated_and_descendant(A, O2)` walks `O2.owner (=O1) -> O1.owner (=A) == A`, within the 8-hop bound, so the check **passes**, and `O1.owner` becomes `O2`.
4. Now `O1.owner == O2` and `O2.owner == O1`: a two-node cycle with no account root.
5. Any subsequent transaction that calls `object::address_to_object<ObjectCore>(O1_or_O2).root_owner()` (e.g. `code::publish_package` to `O1`/`O2` with `LAZY_MODULE_INITIALIZATION` enabled, or any entry function on a module hosted there calling `init::internal_maybe_initialize`) enters the unbounded `while (is_object(obj_owner))` loop in `root_owner()` and aborts only via gas exhaustion, permanently blocking publish/upgrade/self-init for that address.

Note: I did not have execution access to run this end-to-end in the e2e test harness; the trace above is derived directly from reading `object.move`, `code.move`, and `init.move`. A background Devin session with repo execution access could confirm this with a concrete `#[test]` reproducing the cycle and asserting an out-of-gas abort on `root_owner()`.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/object.move (L558-594)
```text
    /// Transfers ownership of the object (and all associated resources) at the specified address
    /// for Object<T> to the "to" address.
    public entry fun transfer<T: key>(
        owner: &signer,
        object: Object<T>,
        to: address,
    ) {
        transfer_raw(owner, object.inner, to)
    }

    /// Attempts to transfer using addresses only. Transfers the given object if
    /// allow_ungated_transfer is set true. Note, that this allows the owner of a nested object to
    /// transfer that object, so long as allow_ungated_transfer is enabled at each stage in the
    /// hierarchy.
    public fun transfer_raw(
        owner: &signer,
        object: address,
        to: address,
    ) {
        let owner_address = signer::address_of(owner);
        verify_ungated_and_descendant(owner_address, object);
        transfer_raw_inner(object, to);
    }

    inline fun transfer_raw_inner(object: address, to: address) {
        let object_core = borrow_global_mut<ObjectCore>(object);
        if (object_core.owner != to) {
            event::emit(
                Transfer {
                    object,
                    from: object_core.owner,
                    to,
                },
            );
            object_core.owner = to;
        };
    }
```

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

**File:** aptos-move/framework/aptos-framework/sources/object.move (L710-737)
```text
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
