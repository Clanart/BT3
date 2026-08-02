## Analysis

The external report's root invariant: a chain of downstream trades/authority must be validated against expected bounds before committing state; without such a bound, code silently walks into an unsafe/uncontrolled state (unbounded price movement → drained funds). The Aptos-native analog I traced to is a **missing bound** in a similar "chain of authority" resolution, specifically in object ownership resolution used by the code-publish path.

`object::owns()` and `object::verify_ungated_and_descendant()` — the two functions that resolve nested-object ownership for transfer authorization — both explicitly bound their traversal to `MAXIMUM_OBJECT_NESTING` because object ownership chains can be cyclic (the code comment itself says so: "objects may have cyclic dependencies"). [1](#0-0) [2](#0-1) 

But `object::root_owner()`, added for the new lazy-module-initialization feature, walks the same ownership chain with **no depth bound at all**: [3](#0-2) 

This function is called directly from the code-publish path (`code::publish_package`, gated by `features::is_lazy_module_initialization_enabled()`) and from `init::assert_may_self_initialize` (the ownership guard for lazy self-init): [4](#0-3) [5](#0-4) 

Crucially, `object::transfer_raw` only validates that the *caller* transitively owns the *source* object being moved — it never checks whether the chosen `to` destination would close a cycle back onto that object or one of its ancestors: [6](#0-5) 

A user who owns two independent objects `A` and `B` directly can do:
1. `transfer(A, B)` — passes, since the caller directly owns `A`.
2. `transfer(B, A)` — passes, since the caller (still) directly owns `B` at that point.

This creates `A.owner == B` and `B.owner == A`, an ownership cycle, using nothing but ordinary permissionless `object::transfer` calls.

### Title
Unbounded loop in `object::root_owner()` allows a self-created object-ownership cycle to permanently DoS code publish/upgrade and lazy self-init — (File: `aptos-move/framework/aptos-framework/sources/object.move`)

### Summary
`object::root_owner()` is used by `code::publish_package` (to record the "deploy owner" of object-hosted modules) and by `init::internal_maybe_initialize`/`assert_may_self_initialize` (to gate lazy module self-initialization). Unlike the sibling functions `owns()` and `verify_ungated_and_descendant()`, which bound their ownership-chain walk to `MAXIMUM_OBJECT_NESTING`, `root_owner()` loops with no bound. Because `object::transfer`/`transfer_raw` never validates that a transfer's destination does not close a cycle back to the object being moved, any account can create a 2-object ownership cycle using only standard `object::transfer` entry calls. Once an object hosting published code is (or becomes) part of such a cycle, every future call into `root_owner()` for that object loops until out-of-gas.

### Finding Description
`code::publish_package` records, for every module in the package, the object's transitive root owner whenever `LAZY_MODULE_INITIALIZATION` is enabled and the destination is an object: [4](#0-3) 

This calls `object::address_to_object<ObjectCore>(addr).root_owner()`, whose implementation is:
```
public fun root_owner<T: key>(self: Object<T>): address {
    let obj_owner = self.owner();
    while (is_object(obj_owner)) {
        obj_owner = address_to_object<ObjectCore>(obj_owner).owner();
    };
    obj_owner
}
``` [7](#0-6) 

There is no iteration counter, unlike `owns()`: [8](#0-7) 

The same unbounded call also gates `init::internal_maybe_initialize`, the entry point compiled-in code uses to lazily self-initialize object-hosted modules: [9](#0-8) 

The ownership graph can be made cyclic because `transfer_raw` only authorizes the move based on the *caller's* ownership of the *source* object; it performs no check on the destination: [1](#0-0) 

An account that owns two objects `A` and `B` can call `object::transfer(A -> B)` then `object::transfer(B -> A)`; both succeed independently because at the time of each call the caller still directly owns the object being moved. The result is `A.owner == B` and `B.owner == A`, a closed cycle where `is_object()` is true for both endpoints forever.

### Impact Explanation
Once code is published to an object address (or a wallet-controlled object later becomes) that participates in an ownership cycle, any subsequent `code::publish_package` call to that address (via `object_code_deployment::upgrade`, `object_code_deployment::publish`, or resource/code-object flows) with the lazy-module-initialization feature enabled will hang in `root_owner()`'s unbounded loop and abort only via gas exhaustion — never completing successfully. The same applies to any entry function relying on `init::internal_maybe_initialize` for self-initialization at that address. This permanently denies the legitimate object owner the ability to upgrade, freeze-after-upgrade, or lazily initialize modules at that address — a code-safety/availability break directly in the publish/upgrade authority path, matching "write-set-publish failure that reaches protected state mutation" and "object code deployment flows must not leak upgrade or freeze authority" (here, by destroying it instead).

### Likelihood Explanation
Creating the two-hop cycle requires only two ordinary `object::transfer` entry-function calls by any unprivileged account that owns two objects — no special privilege, governance, or admin assumption is needed. The only gating factor is the `LAZY_MODULE_INITIALIZATION` feature flag, which is a "transient" flag intended to be enabled; once active, the vulnerable code path is reached by ordinary publish/upgrade/self-init transactions.

### Recommendation
Bound `object::root_owner()`'s traversal to `MAXIMUM_OBJECT_NESTING` (aborting or returning a defined "no acyclic root" result beyond that depth), consistent with `owns()` and `verify_ungated_and_descendant()`. Additionally, consider rejecting `transfer`/`transfer_to_object` calls whose destination would create a cycle including the object being moved, closing the root cause rather than only its symptom in `root_owner()`.

### Proof of Concept
1. Enable `LAZY_MODULE_INITIALIZATION`.
2. As account `U`, create two objects `A` (`object::create_object(U)`) and `B` (`object::create_object(U)`), both with `allow_ungated_transfer = true`.
3. Publish a module to object `A` via `object_code_deployment::publish` (records `deploy_owner = root_owner(A) = U`).
4. `object::transfer(U, A, B)` — succeeds (`U` directly owns `A`); now `A.owner == B`.
5. `object::transfer(U, B, A)` — succeeds (`U` directly owns `B`); now `B.owner == A`. A cycle now exists: `A -> B -> A`.
6. Call `object_code_deployment::upgrade` (or trigger lazy self-init via a function calling `init::internal_maybe_initialize`) targeting object `A`. `code::publish_package`/`init::assert_may_self_initialize` invokes `root_owner(A)`, which loops forever between `A` and `B` (`is_object` is true for both), consuming gas until the transaction aborts with `OUT_OF_GAS`. No further upgrade or self-init to `A` (or `B`) can ever succeed while the cycle persists.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/object.move (L568-594)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/init.move (L54-83)
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
