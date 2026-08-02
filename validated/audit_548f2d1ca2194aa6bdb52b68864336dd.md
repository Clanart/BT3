Based on my research, I was unable to fully verify the exact `transfer`/`transfer_to_object` function bodies (a final tool call failed due to a missing parameter, and no further tool calls are available), so the exploit chain below rests on partially-confirmed evidence. I present it with that caveat rather than fabricating certainty.

### Title
Unbounded traversal in `root_owner()` lets ownership cycles DoS the lazy-module-init publish/upgrade path - (File: `aptos-move/framework/aptos-framework/sources/object.move`)

### Summary
`aptos_framework::object::root_owner()` walks the ownership chain of an object with **no depth bound**, unlike every other ownership-chain walker in the same module (`owns()`, `verify_ungated_and_descendant()`), which both enforce `MAXIMUM_OBJECT_NESTING = 8` and explicitly abort on cycles/deep nesting. [1](#0-0) [2](#0-1) 

`root_owner()` is not a cosmetic view function — it is load-bearing for the **publish path**: `code::publish_package` calls it to record the "deploy owner" of every module published under an object, and `init::assert_may_self_initialize` calls it again on every lazy self-init to compare against that recorded owner. [3](#0-2) [4](#0-3) 

### Finding Description
`verify_ungated_and_descendant(owner, destination)`, which gates `transfer`, only checks that the *transferring signer's address* appears somewhere in `destination`'s existing ownership chain (bounded to 8 hops). It never checks whether the *object being moved* itself appears in that chain. [2](#0-1) 

Consider: object `A` is created directly owned by account `user` (`create_object(user)`), then object `B` is created directly owned by `A` (`create_object(A)`) — both are creations, not transfers, so `verify_ungated_and_descendant` is never invoked for these steps. Now `user` calls `transfer(user, A, B)`. The check walks from `B`'s *pre-transfer* owner chain: `B.owner == A`, `A.owner == user` — it finds `user` within one hop and passes, because at check-time `A`'s ownership hasn't changed yet. The transfer then sets `A.owner = B`, producing `A.owner == B` and `B.owner == A`: a genuine two-object ownership cycle. The existing test suite only demonstrates the single-object self-transfer case being caught (`test_cyclic_ownership_transfer_should_fail`), which is a different, narrower situation (the check catches self-loops because the loop condition never resolves and hits the 8-hop abort during the *second* self-transfer) — it does not cover the two-object indirect case above. [5](#0-4) 

Once such a cycle exists, `root_owner()` — used unconditionally and without any depth cap by `code::publish_package`'s lazy-init owner-recording and by `init::assert_may_self_initialize`'s owner-comparison — loops until gas is exhausted rather than aborting cleanly like `owns()`/`verify_ungated_and_descendant()` do. [6](#0-5) 

### Impact Explanation
If a code object ends up nested inside (or forming a cycle with) another object it also transitively owns, any subsequent `code::publish_package` call under that address (initial publish, upgrade, or `object_code_deployment::upgrade`) that hits the lazy-module-initialization branch will call `root_owner()` and run out of gas instead of failing fast — permanently denying the legitimate code-object owner the ability to publish/upgrade code at that address, and denying `init::internal_maybe_initialize` callers self-init entirely. This is a publish-path availability/code-safety break: it converts an owner-authority check (`EOWNER_CHANGED_SINCE_DEPLOY` gate) into an unconditional DoS instead of a deterministic accept/reject, for any object address that becomes cyclically owned. [3](#0-2) [7](#0-6) 

### Likelihood Explanation
Medium-to-low confidence: I confirmed the asymmetry between `root_owner()` (unbounded) and `owns()`/`verify_ungated_and_descendant()` (bounded, cycle-safe) directly in the source, and confirmed the check's logic only verifies reachability of the *signer's* address, not absence of the *moved object* from the chain. However, I was **not able to read the actual `transfer`/`transfer_to_object` entry-point bodies** (tool call failed with no further retries available) to fully confirm that `verify_ungated_and_descendant` is invoked exactly as I described with no additional guard elsewhere in `transfer()`. This should be verified directly against `transfer<T: key>` and `transfer_to_object` before treating this as confirmed.

### Recommendation
1. Bound `root_owner()` by `MAXIMUM_OBJECT_NESTING` (mirroring `owns()`), aborting deterministically instead of looping unboundedly.
2. In `verify_ungated_and_descendant`, additionally check that the object being transferred does not itself appear in the destination's ownership chain (not just that the signer's address does), to prevent multi-object ownership cycles, not just single-object self-loops.
3. Add regression tests for the indirect (≥2-object) cycle-formation scenario described above, and for `code::publish_package`/`init::internal_maybe_initialize` behavior when the underlying object graph is cyclic.

### Proof of Concept
Conceptual sequence (pending verification against the real `transfer` implementation):
1. `user` calls `object::create_object(user)` → object `A`, `A.owner = user`.
2. `user` calls `object::create_object(A_addr)` → object `B`, `B.owner = A_addr`.
3. `user` calls `object::transfer(user, A, B_addr)`. `verify_ungated_and_descendant(user_addr, B_addr)` walks `B.owner (A) → A.owner (user)`, finds `user` within 1 hop, passes. Transfer sets `A.owner = B_addr`.
4. Chain state is now cyclic: `A.owner == B_addr`, `B.owner == A_addr`.
5. `user` (or the object signer) attempts `code::publish_package` under address `A` (or `B`) with `features::is_lazy_module_initialization_enabled()` on. The call `object::address_to_object<ObjectCore>(addr).root_owner()` loops between `A` and `B` forever, and the transaction runs out of gas instead of failing with a normal `EMAXIMUM_NESTING`-style abort — reproducing the DoS on the publish path.

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
