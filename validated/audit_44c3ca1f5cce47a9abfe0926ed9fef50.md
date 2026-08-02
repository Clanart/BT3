### Title
Unbounded ownership-chain traversal in `object::root_owner()` enables gas-DoS on the publish / lazy module-init path - ([File: aptos-move/framework/aptos-framework/sources/object.move])

### Summary
`aptos_framework::code::publish_package` and `aptos_framework::init::internal_maybe_initialize` (the lazy module self-init gate) both call `object::root_owner()` to record and later validate the transitive owner of an object that hosts published code. Unlike its sibling function `owns()`, which bounds ownership-chain traversal with `MAXIMUM_OBJECT_NESTING`, `root_owner()` has no depth bound at all, so it walks the full parent-ownership chain of an object with no limit on cost.

### Finding Description
`code::publish_package` records, for every object-hosted module, the object's transitive root owner so that a later self-init attempt can detect if ownership changed: [1](#0-0) 

This is done via `object::root_owner()`: [2](#0-1) 

The same call is made every time a module attempts to self-initialize through `init::internal_maybe_initialize` -> `assert_may_self_initialize` -> comparison against `object::address_to_object<ObjectCore>(addr).root_owner()`: [3](#0-2) 

Compare this to `object::owns()`, in the very same module, which walks the identical parent chain but explicitly bounds the loop and aborts once `MAXIMUM_OBJECT_NESTING` is exceeded: [4](#0-3) 

`root_owner()` has no equivalent guard. Because object creation (`object::create_object`, `create_named_object`) and object transfer are fully permissionless, any account can construct an arbitrarily deep chain of nested objects (`ObjectN` owned by `Object(N-1)`, ..., owned by `Object1`, owned by an account) across many low-cost transactions, then designate `ObjectN` as the address that hosts a code package via `object_code_deployment::publish`/`upgrade`. Every subsequent `code::publish_package` call for that address, and every call to any entry function in that module that uses `internal_maybe_initialize`, walks the full N-deep chain with global-storage loads and no protocol-level cutoff, unlike the bounded `owns()` path used elsewhere for ownership checks (e.g. `is_owner`/`freeze_code_object`, `object_code_deployment::upgrade`).

### Impact Explanation
This is a code-safety-adjacent, publish-path finding: it sits directly in the protected write-set mutation flow (`code::publish_package`, which updates `PackageRegistry` and requests native module publish) and in the module-init native validation flow (`init::internal_maybe_initialize`, which mints a signer for state mutation). An attacker (or the object owner themselves, inadvertently) can grow the nesting depth to a point where a single publish/upgrade transaction or a single self-init call is forced to perform O(N) unbounded storage reads within `root_owner()`. Depending on chosen depth this can push execution toward the per-transaction/block gas limit, denying the ability to publish, upgrade, freeze, or self-initialize code hosted at that address — effectively bricking the object-hosted package's upgrade/init flow. This mirrors the OCL-1 pattern (unbounded loop reachable from unauthenticated/unbounded input causing gas exhaustion on a state-mutating path), but here the unbounded input is the on-chain object-ownership graph depth rather than a raw array length.

### Likelihood Explanation
Requires the `lazy_module_initialization` feature flag to be enabled (gated by `features::is_lazy_module_initialization_enabled()`) and requires the code to be hosted at an object address rather than a plain account. Both preconditions are ordinary, supported configurations (`object_code_deployment.move` is a first-class publish path). Building the nested-object chain is cheap and fully permissionless — no privileged action is required, only enough transactions to create the desired nesting depth. I was not able to verify in this pass whether `object::transfer` (not directly retrieved) blocks assigning an object as owner of one of its own ancestors, which would additionally allow a true cycle (infinite loop rather than merely deep-but-finite traversal); this should be independently confirmed. Even without a true cycle, the unbounded linear-depth case alone is sufficient to demonstrate the missing bound.

### Recommendation
Add the same depth bound used in `owns()` to `root_owner()` (e.g., cap traversal at `MAXIMUM_OBJECT_NESTING` and abort with a clear error code on overflow), and ensure `object::transfer`/ownership-assignment code rejects any transfer that would create a cycle in the ownership graph. Additionally consider caching/limiting the recorded root owner rather than recomputing full-depth traversal on every publish and every self-init call.

### Proof of Concept
1. With `lazy_module_initialization` enabled, create a chain of N nested objects: `create_object(@attacker)` -> `O1`; `create_object(O1)` -> `O2`; ... up to `O_N`, transferring each successive object under the prior one as owner (each step is a cheap, independent transaction).
2. Publish a module to `O_N` via `object_code_deployment::publish`. Inside `code::publish_package`, `object::address_to_object<ObjectCore>(O_N).root_owner()` walks all N hops in this single transaction (code.move line 183).
3. Alternatively, have the published module call `init::internal_maybe_initialize` in an entry function; each invocation re-walks the N-hop chain via `assert_may_self_initialize`/`recorded_deploy_owner` comparisons (init.move lines 74-83).
4. Increase N until the gas consumed by the traversal step of a single publish/upgrade or self-init transaction approaches the transaction/block gas limit, demonstrating unbounded per-call cost with no protocol-enforced ceiling (contrast with `owns()`, which aborts deterministically at `MAXIMUM_OBJECT_NESTING`).

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
