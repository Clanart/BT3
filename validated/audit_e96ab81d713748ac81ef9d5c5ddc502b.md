## Title
Missing nesting-depth bound in `object::root_owner()` allows an unprivileged actor to construct a cyclic object-ownership graph that permanently DoS's the code-publish / lazy module-init ownership gate - (File: `aptos-move/framework/aptos-framework/sources/object.move`)

### Summary
`object::root_owner()` walks the object ownership chain without any depth bound or cycle protection, unlike the two other ownership-chain walkers in the same file (`owns()` and `verify_ungated_and_descendant()`), which both cap iteration at `MAXIMUM_OBJECT_NESTING` (8). Because `object::transfer()`/`transfer_to_object()` only validates that the caller controls the object being moved — it never checks whether the destination is itself (transitively) owned by the object being moved — an ordinary, unprivileged user can create a genuine ownership cycle between two objects they own. `root_owner()` is then called directly from the code-publishing path (`code::publish_package`) and from the lazy module self-init gate (`init::assert_may_self_initialize`), so any code object caught in such a cycle can never publish, upgrade, freeze, or self-initialize again — the call spins until gas is exhausted every single time.

### Finding Description
`root_owner()`: [1](#0-0) 

has no bound, in contrast to `owns()`: [2](#0-1) 

and `verify_ungated_and_descendant()`, which is the function actually used to gate `object::transfer`: [3](#0-2) 

`verify_ungated_and_descendant(owner, destination)` only checks that `owner` appears somewhere in the ownership chain of `destination` (i.e. that the signer really controls the object being moved); it never inspects whether the new parent (`to`) is itself owned, directly or transitively, by the object being transferred. Combined with `transfer_to_object`/`transfer`: [4](#0-3) 

a normal user can:
1. Own objects `A` and `B` (e.g. two code objects created via `object_code_deployment::publish`).
2. Transfer `A` into `B` (`A.owner = B`) — passes, since the caller directly owns `A`.
3. Transfer `B` into `A` (`B.owner = A`) — also passes, since the caller still directly owns `B` at that point (the check only looks at `B`'s own chain, which still resolves to the caller).

This produces `A.owner == B` and `B.owner == A`, a permanent 2-cycle. Any subsequent call to `root_owner()` on `A` or `B` loops forever (`is_object(obj_owner)` is always true).

`root_owner()` is called from the security-relevant publish/init paths:
- `code::publish_package`, which records the deploy owner for lazy module init on every (re)publish/upgrade of object-hosted code: [5](#0-4) 
- `init::assert_may_self_initialize`, the ownership gate that decides whether `internal_maybe_initialize` may mint a signer for an object address: [6](#0-5) 

Both of these are on the object-code-deployment publish/upgrade/self-init flow used by `object_code_deployment::publish`/`upgrade`: [7](#0-6) 

### Impact Explanation
Once a code object's ownership graph contains a cycle, every transaction that needs to determine its `root_owner()` (publishing new code to it, upgrading it, or any module on it calling `internal_maybe_initialize`) will loop until it runs out of gas and aborts — permanently. This breaks the code-object publish/upgrade/init invariant that legitimate owners can always manage and upgrade their own code objects: the object becomes permanently un-upgradable/un-initializable, i.e. a targeted, self-inflicted or third-party-inflicted bricking of the object-code-deployment publish path. Since `code::publish_package` unconditionally calls `root_owner()` for any object address whenever the lazy-module-initialization feature is enabled, this also means an attacker who is handed (or who buys/receives) a code object with a hidden pre-built cycle inherits a package that can never be upgraded or frozen again.

### Likelihood Explanation
Triggering this requires only two ordinary, permissionless `object::transfer` calls on objects the caller already owns — no special privileges, no governance, no race condition. Any Aptos account can construct the cycle deterministically today using standard object/code-object APIs.

### Recommendation
Bound `root_owner()`'s traversal with the same `MAXIMUM_OBJECT_NESTING` cap (and clear failure semantics) used by `owns()` and `verify_ungated_and_descendant()`, and/or reject transfers in `verify_ungated_and_descendant`/`transfer_raw` whenever the destination is found to be a (transitive) descendant of the object being moved, closing the root cause of cycle creation rather than only bounding its consequence.

### Proof of Concept
1. Account `U` creates two objects `A` and `B` (e.g. via `object::create_object(U)` twice, or two `object_code_deployment::publish` calls), both owned by `U`, both with `allow_ungated_transfer = true`.
2. `U` calls `object::transfer(U, A, B_address)` → succeeds (`A.owner = B`).
3. `U` calls `object::transfer(U, B, A_address)` → succeeds because `verify_ungated_and_descendant(U, B)` only checks that `B`'s chain reaches `U` (`B.owner` is still `U` at this point) — it never checks that `A_address` (the new parent for `B`) is a descendant of `B`. Now `B.owner = A`, giving `A.owner = B`, `B.owner = A`.
4. Any later call to `A.root_owner()` or `B.root_owner()` — e.g. by `code::publish_package` when `U` tries to upgrade module code on `A`, or by a module on `A`/`B` calling `init::internal_maybe_initialize` — loops until gas exhaustion and the transaction aborts, permanently blocking any further publish/upgrade/self-init on either object.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/object.move (L571-603)
```text
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

    /// Transfer the given object to another object. See `transfer` for more information.
    public entry fun transfer_to_object<O: key, T: key>(
        owner: &signer,
        object: Object<O>,
        to: Object<T>,
    ) {
        transfer(owner, object, to.inner)
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

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L80-132)
```text
    public entry fun publish(
        publisher: &signer,
        metadata_serialized: vector<u8>,
        code: vector<vector<u8>>,
    ) {
        let publisher_address = signer::address_of(publisher);
        let object_seed = object_seed(publisher_address);
        let constructor_ref = &object::create_named_object(publisher, object_seed);
        let code_signer = &constructor_ref.generate_signer();
        code::publish_package_txn(code_signer, metadata_serialized, code);































            object::is_owner(code_object, publisher_address),
            error::permission_denied(ENOT_CODE_OBJECT_OWNER),
        );

        let code_object_address = code_object.object_address();
        assert!(exists<ManagingRefs>(code_object_address), error::not_found(ECODE_OBJECT_DOES_NOT_EXIST));

        let extend_ref = &borrow_global<ManagingRefs>(code_object_address).extend_ref;
        let code_signer = &extend_ref.generate_signer_for_extending();
        code::publish_package_txn(code_signer, metadata_serialized, code);

        event::emit(Upgrade { object_address: signer::address_of(code_signer), });
```
