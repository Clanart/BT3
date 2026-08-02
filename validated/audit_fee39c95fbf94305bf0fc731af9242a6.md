### Title
Unbounded ownership-chain walk in `object::root_owner` enables a cyclic-ownership DoS that permanently blocks code-object publish/upgrade and lazy module self-init - (File: `aptos-move/framework/aptos-framework/sources/object.move`)

### Summary
The Aptos code-publishing/self-init security guard relies on `Object::root_owner()` to fetch the "true" (transitive, non-object) owner of a code object, both when recording the deploy owner at publish time (`code::publish_package`) and when validating it before minting a lazy self-init signer (`init::assert_may_self_initialize`). Unlike every other ownership-chain walker in the same module (`owns`, `verify_ungated_and_descendant`), `root_owner()` has **no cycle guard and no `MAXIMUM_OBJECT_NESTING` depth bound**. Because `object::transfer_raw` only verifies that the caller is an ancestor of the object being moved — it never checks whether the chosen destination is a descendant of that same object — a caller can construct a two-object ownership cycle (`A` owned by `B`, `B` owned by `A`). Any subsequent call into the publish/upgrade/self-init path for a module hosted on `A` or `B` invokes `root_owner()`, which loops forever (until gas exhaustion), permanently bricking upgrade, freeze, and lazy self-init for that code object.

### Finding Description
`root_owner` walks the ownership chain with no termination guard other than `is_object`: [1](#0-0) 

Compare this to the two other ownership-chain walkers in the same file, both of which explicitly bound the walk with `MAXIMUM_OBJECT_NESTING` to guard against exactly this class of issue: [2](#0-1) [3](#0-2) 

The transfer path that changes ownership only validates that the transferring signer is an *ancestor* of the object being moved; it does not check that the new owner (`to`) is not itself a *descendant* of the object: [4](#0-3) 

This makes it possible to construct a 2-cycle: create object `A` (owned by `X`), create object `B` owned by `A`, then call `transfer_raw(X, A, B)`. `verify_ungated_and_descendant(X, A)` succeeds (X is A's direct owner), so `A`'s owner is set to `B`. The result: `A → B → A`, a cyclic ownership graph that no existing check rejects.

`root_owner()` is exactly the function used by the code-publishing security controls introduced for object-hosted modules:

- Recording the deploy owner on every (re)publish: [5](#0-4) 

- Validating the recorded owner before minting a lazy self-init signer: [6](#0-5) 

Both call sites invoke `object::address_to_object<ObjectCore>(addr).root_owner()` unconditionally. If `addr`'s ownership graph contains a cycle, this call never terminates and the enclosing transaction will run out of gas every time it is attempted.

### Impact Explanation
Once a code object's ownership graph contains a cycle:
- Every call to `code::publish_package` for that object (i.e., every `object_code_deployment::upgrade` and `code::freeze_code_object` — since `freeze_code_object` also calls `check_dependencies`/root_owner-adjacent registry logic through republish flows) that has `LAZY_MODULE_INITIALIZATION` enabled will attempt to compute `root_owner()` and exhaust gas, aborting the transaction. This permanently removes the ability to upgrade or freeze the package at that object address — the owner-authority/upgrade path is effectively bricked with no recovery, since the cyclic ownership state itself cannot be fixed (any further `transfer` also requires walking the same broken/looping chain in some code paths, or requires the same ancestor verification which is unaffected but doesn't fix `root_owner`).
- Every call to a module's entry function that relies on `init::internal_maybe_initialize` for that object address will hit `assert_may_self_initialize` → `root_owner()` → an unbounded loop, permanently preventing the module from ever completing its lazy self-initialization.

This is a genuine, unprivileged, locally-reachable denial-of-service against the publish/upgrade/freeze/self-init authority of a code object — a caller with no special privileges other than owning two objects can construct the cyclic structure themselves (e.g., as a "trap" transferred to or inherited by another party, or by mistake), permanently disabling code-object governance for the affected address. Given code-object publish/upgrade/freeze is a mainnet-relevant, security-critical code path, and the effect is a hard, irreversible loss of upgrade/freeze/init authority, this is High severity.

### Likelihood Explanation
Constructing the cycle requires only ordinary, permissionless calls (`object::create_object`, `object::transfer`/`transfer_raw`) that are already exposed to all users; no admin or governance action is required. The only precondition is that `features::is_lazy_module_initialization_enabled()` is on (needed to reach the vulnerable `root_owner()` call in `code::publish_package`) — this is a mainnet feature flag, not an out-of-scope governance/permission assumption, since the exploit itself requires no privileged actor. Likelihood is therefore Medium-High: straightforward to construct, but requires the target code object's ownership to actually be routed through a 2-object cycle, which typically requires either self-infliction or a social/contract-design trick that gets a legitimate owner's object nested in this way.

### Recommendation
Add a `MAXIMUM_OBJECT_NESTING`-bounded loop (mirroring `owns` and `verify_ungated_and_descendant`) to `root_owner`, aborting with a defined error (e.g., `EMAXIMUM_NESTING`) if the bound is exceeded, and/or reject `transfer`/`transfer_raw` calls whose destination is a descendant of the object being moved (cycle-prevention at write time) rather than only catching it at read time.

### Proof of Concept
1. `X` calls `object::create_object(X)` → object `A` (owner `X`).
2. `X` calls `object::create_object(A)` (as `A`'s signer, e.g. via `ConstructorRef`) → object `B` (owner `A`).
3. `X` calls `object::transfer_raw(X_signer, A, B)`. `verify_ungated_and_descendant(X, A)` succeeds because `A`'s current owner is `X`. `A`'s owner is now set to `B`, producing the cycle `A → B → A`.
4. Deploy a module (with `LAZY_MODULE_INITIALIZATION` enabled) to object `A` via `object_code_deployment::publish`, or ensure a module's owner-recording flows through `A`/`B`.
5. Any subsequent call to `object_code_deployment::upgrade`, `code::freeze_code_object`, or any entry function using `init::internal_maybe_initialize` for that module invokes `object::address_to_object<ObjectCore>(A).root_owner()` (or `B`'s), which loops forever inside `while (is_object(obj_owner)) { ... }`, causing the transaction to run out of gas and abort — permanently blocking upgrade, freeze, and self-init for that code object. [7](#0-6)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/object.move (L558-580)
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
