## Analysis Summary

The strongest local analog I found is a mismatch between two object-ownership-graph walkers introduced/relied upon by the new publish-time ownership tracking (`code::publish_package` → `init::record_deploy_owner` / `init::assert_may_self_initialize`), and the object module's incomplete cycle prevention on transfer.

### Title
Unbounded `object::root_owner` loop combined with cycle-forming `object::transfer_raw` breaks the publish-time deploy-owner gate — (File: `aptos-move/framework/aptos-framework/sources/object.move`)

### Summary
`code::publish_package` records, for object-hosted modules, the object's **transitive root owner** at publish time via `object::root_owner()`, and `init::assert_may_self_initialize` later re-derives `root_owner()` to decide whether a module may mint its own signer via lazy self-initialization [1](#0-0) [2](#0-1) . Unlike every other ownership-chain walker in the object model (`owns`, `verify_ungated_and_descendant`), which caps traversal at `MAXIMUM_OBJECT_NESTING`, `root_owner` has **no depth bound**: [3](#0-2) . Meanwhile, `transfer_raw`'s `verify_ungated_and_descendant` only checks that the *destination*'s existing owner-chain reaches back to the caller — it never checks that the object being moved is not itself an ancestor of the destination [4](#0-3) . An owner who legitimately controls an entire object chain (A owns Z, Z owns Y, Y owns X) can call `transfer(Z, to=X)`; the check only verifies X's chain reaches A (true), so it succeeds and creates the cycle X→Y→Z→X.

### Finding Description
Once such a cycle exists at or above an object hosting a module published with the lazy-init feature, any subsequent call to `init::internal_maybe_initialize` for that module invokes `assert_may_self_initialize` → `object::root_owner()` [5](#0-4) , which loops forever (bounded only by gas exhaustion) because the `while (is_object(obj_owner))` condition never becomes false. This permanently breaks the owner-unchanged invariant the feature is built to enforce: the code-safety gate that "an object-hosted module can only self-initialize while ownership is unchanged since publish" can be forced into unconditional failure/gas-griefing by anyone controlling the ownership chain, using an ordinary, unprivileged `object::transfer`/`transfer_call` entry point.

### Impact Explanation
This directly corrupts the protected state-mutation gate added for object-code publish ownership tracking: it is not incidental network DoS but a break of the specific invariant `code.move`/`init.move` were built to guarantee (self-init authorized only while root owner is unchanged). The owner of an object housing published code can turn the module's own lazy self-init entry point into a permanent abort for all future callers (including the legitimate owner), and the same unbounded loop is reachable by any other future caller of `object::root_owner` on an attacker-constructed object graph, since nothing prevents a self-owning cycle.

### Likelihood Explanation
High. Constructing the cycle requires only ordinary object creation and transfer calls (`object::transfer_call`) by an account that owns a chain of objects it created itself — no special privileges, and `verify_ungated_and_descendant`'s check passes because it never inspects whether the object being moved appears in the destination's ancestor chain.

### Recommendation
1. Bound `object::root_owner`'s traversal by `MAXIMUM_OBJECT_NESTING`, aborting with `EMAXIMUM_NESTING` like `owns`/`verify_ungated_and_descendant` do.
2. Prevent cycle formation in `transfer_raw`/`verify_ungated_and_descendant` by also asserting that `object` (the address being moved) is not itself found while walking up from `to`.

### Proof of Concept
1. Account `A` creates objects `Z`, `Y`, `X` such that `Z` owns `Y`, `Y` owns `X` (each `create_object` + `transfer_call`), with `A` owning `Z` initially.
2. `A` publishes a module to object `X` using object-code deployment with `LAZY_MODULE_INITIALIZATION` enabled; `code::publish_package` records `root_owner(X) == A` via `init::record_deploy_owner` [6](#0-5) .
3. `A` calls `object::transfer_call(Z, to=X)`. `verify_ungated_and_descendant(A, X)` walks `X→Y→Z→A`, finds `A`, and succeeds, setting `Z`'s owner to `X`. The chain is now `X→Y→Z→X` (cyclic).
4. Any call into the module on `X` that invokes `init::internal_maybe_initialize` now calls `assert_may_self_initialize` → `root_owner(X)`, which loops `X→Y→Z→X→Y→Z→…` without termination, exhausting gas on every call — permanently denying the module's self-init to its own legitimate owner.

Note: I was not able to independently trace every VM-level `deserialize_module_bundle`/`validate_publish_request` code path (its full body was outside the read window) to rule out an unrelated metadata/bytecode-mismatch bug in that function; based on what was visible it appeared to correctly gate module names and dependencies, so I did not pursue it as the primary finding.

### Citations

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
