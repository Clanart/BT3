### Title
Unbounded traversal in `Object::root_owner()` allows an attacker-constructed ownership cycle to break the lazy module self-init ownership gate - (File: `aptos-move/framework/aptos-framework/sources/object.move`)

### Summary
`object::root_owner()`, which is the sole primitive used by `aptos_framework::code::publish_package` and `aptos_framework::init::assert_may_self_initialize` to decide whether a code object's owner has changed since a module was last published, walks the ownership chain with **no cycle/depth bound**, unlike every other ownership-chain traversal in the same module (`owns()` and `verify_ungated_and_descendant()`), which both cap iterations with `MAXIMUM_OBJECT_NESTING`. Because `object::transfer`/`transfer_raw` only validates that the caller is an (indirect) owner of the object being moved and never validates the destination address, a user who controls two objects can transfer them into each other and construct an ownership cycle. Any subsequent call to `root_owner()` on that cycle (which is invoked from the publish path and from `init::internal_maybe_initialize`) loops forever instead of terminating, unlike the guarded siblings.

### Finding Description
`code::publish_package` records, for lazy-module-initialization purposes, the *transitive root owner* of the object hosting a package at (re)publish time: [1](#0-0) 

That recorded owner is later compared, again via `root_owner()`, in `init::assert_may_self_initialize`, which gates whether a module hosted on an object may mint its own signer via `internal_maybe_initialize`: [2](#0-1) 

Both call sites depend on `object::root_owner()`: [3](#0-2) 

Unlike this function, the two other ownership-chain walkers in the same module explicitly bound the loop with `MAXIMUM_OBJECT_NESTING` to defend against cycles/malformed chains: [4](#0-3) [5](#0-4) 

The transfer primitives that mutate `ObjectCore.owner` only check that the *caller* is an (indirect) owner of the object being transferred; they never validate anything about the `to` destination: [6](#0-5) 

Because `verify_ungated_and_descendant` only requires that walking up from `object`'s *current* owner eventually reaches the caller, a caller who owns two objects `A` and `B` can:
1. `transfer(caller, A, B_address)` — legal, caller currently owns `A`.
2. `transfer(caller, B, A_address)` — legal, caller currently owns `B` (this hasn't changed).

This produces `A.owner == B` and `B.owner == A`, a 2-node ownership cycle, with no check preventing it anywhere in the transfer path.

### Impact Explanation
Once such a cycle exists, `root_owner()` on `A` or `B` never returns — the `while (is_object(obj_owner))` loop keeps re-entering the cycle indefinitely. This directly corrupts the code-safety invariant the report's bug class targets: a value (`root_owner`) that is supposed to unambiguously identify "who controls this code object" is undefined/non-terminating for a reachable on-chain state.
- In a transaction context (e.g. `code::publish_package` republishing to such an object, or a module calling `init::internal_maybe_initialize`), the loop burns gas until the VM's gas meter aborts the transaction — a targeted, cheap self-inflicted DoS of any object stuck in the cycle, permanently preventing that code object from ever being republished/self-initialized (since `record_deploy_owner`/`assert_may_self_initialize` cannot make progress).
- `root_owner` is also a `#[view]` function, callable off-chain (through node view/query endpoints) with no innate Move gas ceiling applied the same way; an unbounded loop reachable from user-supplied on-chain state that "wallet/indexer/verifier" tooling can trigger by simply looking up a specifically crafted address is a genuine service availability issue for any component that calls `root_owner` (including the publish-safety logic in `code.move`/`init.move` itself).
- The underlying flaw is a code-safety/ownership consistency defect: `root_owner`, the trust anchor for the freshly-added self-init object-ownership gate, does not satisfy the invariant "always terminates on a legal on-chain object graph" that its sibling functions do enforce, so the ownership check it backs can be forced into an undefined (non-terminating) state.

### Likelihood Explanation
Constructing the cycle requires nothing privileged: any account that creates two ordinary objects with default ungated-transfer settings (the default `allow_ungated_transfer = true`) can execute two ordinary `object::transfer_call`/`transfer` entry-function calls to link them into a cycle, entirely with its own signer. No governance, no admin key, no race condition is needed. The only pre-condition is that the objects being linked keep `allow_ungated_transfer` enabled at each hop, which is the default.

### Recommendation
Add the same cycle/depth guard already used in `owns()` and `verify_ungated_and_descendant()` to `root_owner()`, i.e., bound the loop with `MAXIMUM_OBJECT_NESTING` and abort (or return a sentinel) if the object graph does not terminate. Independently, consider validating the destination address in `transfer_raw`/`transfer_to_object` to reject transfers that would create an ownership cycle (e.g., disallow `to` being a descendant of `object`), since a cyclic ownership graph is not a valid state for any consumer that assumes ownership chains terminate at a non-object account address.

### Proof of Concept
```
// Pseudo-steps executable by a single unprivileged account `attacker`:
1. cref_a = object::create_object(attacker);      // A owned by attacker
2. cref_b = object::create_object(attacker);      // B owned by attacker
   addr_a = object::address_from_constructor_ref(&cref_a);
   addr_b = object::address_from_constructor_ref(&cref_b);

3. object::transfer_call(&attacker_signer, addr_a, addr_b);
   // now: A.owner == B   (legal: attacker still transitively owned A)

4. object::transfer_call(&attacker_signer, addr_b, addr_a);
   // now: B.owner == A   (legal: attacker still directly owned B)
   // Cycle formed: A -> B -> A

5. object::address_to_object<ObjectCore>(addr_a).root_owner();
   // while(is_object(obj_owner)) never becomes false -> infinite loop,
   // consumes all gas in a transaction context (e.g. if code::publish_package
   // or init::internal_maybe_initialize is invoked against addr_a/addr_b),
   // or hangs a #[view] query off-chain.
```
Note: I was not able to execute this against a live/local Move VM within this analysis (no sandbox access here); the control-flow gap is confirmed by direct code comparison — `root_owner` lacks the `count < MAXIMUM_OBJECT_NESTING` guard present in `owns` and `verify_ungated_and_descendant`, and `transfer_raw` performs no validation of the `to` destination — so a background Devin session with repo/test access is recommended to run the above sequence in `aptos-move/e2e-move-tests` and confirm the non-termination/gas-exhaustion empirically.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/code.move (L182-187)
```text
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
