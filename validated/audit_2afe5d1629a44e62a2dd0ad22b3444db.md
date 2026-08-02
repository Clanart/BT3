## Title
`object::root_owner` lacks cycle/depth bound, enabling permanent gas-exhaustion DoS of code-object publish, upgrade, and lazy module self-init - (File: `aptos-move/framework/aptos-framework/sources/object.move`)

### Summary
`object::root_owner` walks the object-ownership chain to find the top-level (non-object) owner, but unlike every other ownership-traversal function in the same module, it has no depth/cycle bound. Because `object::transfer`'s cycle protection (`verify_ungated_and_descendant`) only validates the *pre-transfer* ownership chain, a sequence of two legitimate transfers can put an object (or a pair of objects) into a permanent mutual ownership cycle. `root_owner` is called from two security-critical, publish-path functions — `code::publish_package` (to record the deploy owner for lazy self-init) and `init::assert_may_self_initialize` (the gate that authorizes a module to obtain its own signer). Once a code object is caught in such a cycle, every call into these functions loops without terminating until the transaction exhausts its gas, permanently bricking publish/upgrade/freeze operations and lazy module initialization for that object.

### Finding Description
`root_owner` is defined as: [1](#0-0) 

Compare this to the two other ownership-chain walkers in the same file, `owns` and `verify_ungated_and_descendant`, both of which explicitly bound the walk to `MAXIMUM_OBJECT_NESTING` (8) and abort otherwise: [2](#0-1) [3](#0-2) 

`root_owner` is missing this bound entirely.

`object::transfer`/`transfer_raw` only check the ownership chain *before* the transfer takes effect (via `verify_ungated_and_descendant`, which traces from the destination up to the current signer). This validates that the *current* transfer doesn't create an immediately-obvious loop back through the signer, but it does not prevent building up a cycle across *multiple* transfers. As the module's own regression test documents, a single self-transfer already creates a genuine on-chain self-loop: [4](#0-3) 

The same construction generalizes: an object owner controlling two objects `A` and `B` can (1) transfer `A` to `B` while `B` is still owned by the signer (passes the check trivially), then (2) transfer `B` to `A`; at that moment `A`'s owner is `B`, and tracing `B`'s chain still reaches the signer (since `B`'s owner hasn't been mutated yet), so the check passes and `B.owner` is set to `A`. The result is a permanent 2-cycle: `A → B → A`.

This directly poisons the two publish-path consumers of `root_owner`:
- `code::publish_package` calls `root_owner()` on the object address being (re)published to record the deploy owner used for lazy self-init gating: [5](#0-4) 
- `init::assert_may_self_initialize` calls `root_owner()` on every lazy self-init check performed by `init::internal_maybe_initialize`, which is invoked by ordinary entry functions of the deployed module: [6](#0-5) 

Once the code object (or an object in its ownership ancestry) is placed in such a cycle, `is_object(obj_owner)` never becomes false and `obj_owner` never changes, so the `while` loop runs until the transaction's gas is exhausted, aborting every transaction that reaches it.

### Impact Explanation
Any transaction that needs to:
- publish or upgrade a package hosted on the affected object (`code::publish_package`, reached via `object_code_deployment::publish`/`upgrade`), or
- call any entry function of that module that relies on `init::internal_maybe_initialize` for lazy self-init,

will deterministically run out of gas and abort. This permanently bricks the code object's upgrade/freeze path and denial-of-services every user of that module's lazily-initializing entry points — a code-safety/state-mutation failure squarely in the object-owned code-publish and module-init flow, not a generic network-level DoS. Because the cycle, once created, cannot be undone (any further transfer attempt aborts via `verify_ungated_and_descendant`'s bound, per the existing test), the effect is permanent for that address.

### Likelihood Explanation
The precondition is that the attacker (typically the object owner or someone who controls the ownership chain, e.g. a resource/multisig account, marketplace escrow flow, or any protocol that programmatically transfers objects it controls) can perform two ordinary, otherwise-legitimate `object::transfer` calls. No special privilege beyond normal object-transfer capability is required, and the feature (`LAZY_MODULE_INITIALIZATION`) plus object-code-deployment are both mainnet-relevant, generally available flows. This makes the bug straightforward to trigger deliberately (e.g., by a malicious package deployer wanting to grief future upgraders, or to permanently disable freeze/upgrade accountability while still allowing the package to appear "live").

### Recommendation
Add the same bound used in `owns`/`verify_ungated_and_descendant` to `root_owner`, aborting (or returning a safe fallback) once `MAXIMUM_OBJECT_NESTING` hops are exceeded:
```move
public fun root_owner<T: key>(self: Object<T>): address {
    let obj_owner = self.owner();
    let count = 0;
    while (is_object(obj_owner)) {
        count += 1;
        assert!(count < MAXIMUM_OBJECT_NESTING, error::out_of_range(EMAXIMUM_NESTING));
        obj_owner = address_to_object<ObjectCore>(obj_owner).owner();
    };
    obj_owner
}
```
Additionally, consider hardening `verify_ungated_and_descendant`/`transfer` to detect cycles across multiple transfers (e.g., by checking that the destination's *current* transitive root does not already equal the object itself), rather than relying solely on a single-transfer chain check.

### Proof of Concept
1. Enable `LAZY_MODULE_INITIALIZATION` and deploy a package via `object_code_deployment::publish` to object `A`, owned by attacker.
2. Attacker creates a second object `B` (also owned by attacker).
3. Attacker calls `object::transfer(attacker, A, B_address)` — succeeds, `A.owner = B`.
4. Attacker calls `object::transfer(attacker, B, A_address)` — succeeds (the check traces `A.owner = B`, `B.owner = attacker`, matching signer), setting `B.owner = A`. Now `A.owner = B` and `B.owner = A`: a permanent 2-cycle.
5. Any subsequent call to `object_code_deployment::upgrade`/`freeze_code_object` targeting `A`, or any user calling an entry function of the module at `A` that invokes `init::internal_maybe_initialize`, triggers `root_owner()` on `A`, which loops indefinitely between `A` and `B` and aborts only once gas is exhausted — permanently denying publish/upgrade/freeze and module self-init for the package at `A`.

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
