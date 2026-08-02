## Title
Unbounded ownership-cycle traversal in `object::root_owner()` lets an object owner permanently brick code-object publish/upgrade/self-init for whoever later controls the object - (File: `aptos-move/framework/aptos-framework/sources/object.move`)

### Summary
`aptos_framework::code::publish_package` (the entry point used by both direct account publishing and by `object_code_deployment::publish`/`upgrade` for code-object deployments) calls `object::root_owner()` to record the "deploy owner" used by the new lazy module-initialization feature. `aptos_framework::init::assert_may_self_initialize` calls the same function on every self-init. Unlike its sibling traversal functions in the same module (`owns`, `verify_ungated_and_descendant`), `root_owner()` has no bound on how many hops it walks and no cycle detection, while nothing in the object-transfer path prevents an owner from creating a cyclic ownership graph.

### Finding Description
`code::publish_package` records the transitive root owner of an object-hosted package for later self-init gating: [1](#0-0) 

`init::assert_may_self_initialize`, used on every `init::internal_maybe_initialize` call (i.e. every lazy self-init of a module), also calls `root_owner()`: [2](#0-1) 

`root_owner()` itself has an unbounded loop with no cycle/depth protection: [3](#0-2) 

Contrast this with the two other ownership-traversal functions in the same module, both of which explicitly cap iterations at `MAXIMUM_OBJECT_NESTING` and abort if exceeded: [4](#0-3) [5](#0-4) 

Critically, `transfer_raw`/`verify_ungated_and_descendant` only authenticates that the *calling signer* is a legitimate (in-chain) owner of the object being moved; it never checks that the transfer *destination* is not itself a descendant of the object being moved: [6](#0-5) 

This means a signer who legitimately owns object `A` directly, and who also owns object `B` (with `B.owner = A`), can call `transfer(A_owner_signer, A, B_address)`, which passes `verify_ungated_and_descendant` trivially (0 hops, since `A`'s current owner is the calling signer), and results in `A.owner = B` while `B.owner = A` — a two-node ownership cycle. Nothing in the codebase rejects this.

Once such a cycle exists at (or beneath, in the ownership tree of) an object hosting a code package, every subsequent call into `code::publish_package` for an object address that is lazy-init enabled, and every call to `init::internal_maybe_initialize` for any module on that object, invokes `root_owner()`, which loops forever walking the cycle. Since it is not gas-metered per "logical hop" the way ordinary Move loops are charged normal gas per bytecode instruction, but is a pure infinite loop, any transaction reaching this code path will simply burn gas until it runs out and aborts — permanently and irrecoverably preventing:
- publishing/upgrading the code object's package (`code::publish_package_txn`, `object_code_deployment::upgrade`), and
- lazy self-initialization of any module on that object.

### Impact Explanation
This directly affects the code-publish invariant: it permanently disables an object-code-object's `code::publish_package` (upgrade) path and `init::internal_maybe_initialize` (self-init) path with no recovery mechanism (there is no repair/cycle-breaking function), effectively an unauthorized, silent, irreversible "freeze" of a code object that bypasses the intended, auditable `freeze_code_object` mechanism. Because ownership of an object can be transferred to third parties (e.g. objects are tradable/sellable), a malicious prior owner can pre-poison the ownership subgraph before transferring control (or a sub-object) to a victim, so the victim discovers — only when they try to publish/upgrade or the module tries to self-init — that the code object is permanently bricked. This matches the "code-object ownership change" and "write-set-publish failure that reaches protected state mutation" categories in the publish impact gate.

### Likelihood Explanation
Creating the two-object cycle requires only a signer who already legitimately owns both objects in the cycle at the time of the second transfer — no special privilege beyond normal `object::transfer` usage is required, and no validation in `object.move` prevents it. The precondition (the victim's package being hosted on an object whose ownership subgraph the attacker previously controlled) is realistic wherever code objects, or objects that end up nested under them, change hands (marketplaces, admin handoffs, DAOs distributing object ownership, etc.). The feature is gated behind `FeatureFlag::LAZY_MODULE_INITIALIZATION`; its exact mainnet rollout status could not be confirmed from the available index (the file `types/src/on_chain_config/aptos_features.rs` was found but its content, showing whether the flag defaults on for mainnet, was not retrievable within the tool budget).

### Recommendation
- Bound `root_owner()`'s traversal by `MAXIMUM_OBJECT_NESTING` (mirroring `owns`/`verify_ungated_and_descendant`) and either abort or return a well-defined sentinel when the bound is exceeded, so a caller (including `code::publish_package` and `init::assert_may_self_initialize`) fails predictably instead of looping.
- Separately, close the root cause in `object::transfer_raw`: reject transfers where the destination `to` is a descendant (per `owns`) of the object being moved, preventing cycle creation entirely.
- Add regression tests constructing a cyclic ownership graph and asserting that `root_owner`, `code::publish_package` (object path), and `init::internal_maybe_initialize` all abort cleanly rather than looping.

### Proof of Concept
1. As account `X`, create object `A` (`X` owns `A` directly) and publish a package to `A` with the lazy-module-initialization feature enabled (via `object_code_deployment::publish` or `code::publish_package_txn` at an object address), so `code::publish_package` records `deploy_owner` via `root_owner()`.
2. As `X`, create object `B` and call `object::transfer(X, B, A_address)` — `B.owner = A`. This succeeds since `X` is `B`'s current owner.
3. As `X` (still direct owner of `A`), call `object::transfer(X, A, B_address)` — `A.owner = B`. This succeeds since `verify_ungated_and_descendant` only checks that `A`'s current owner is `X`, not that `B` is unrelated to `A`. Ownership graph is now `A -> B -> A` (cycle).
4. `X` transfers logical/administrative control of `A` (e.g. via any higher-level mechanism that relies on `A`'s owner) to a victim, or simply attempts a legitimate follow-up action.
5. Any subsequent call to `code::publish_package_txn` targeting `A` (e.g. `object_code_deployment::upgrade`), or any entry function on a module hosted at `A` that calls `init::internal_maybe_initialize`, invokes `object::root_owner()` on `A`, which loops indefinitely over `A -> B -> A -> B -> ...` until the transaction exhausts its gas limit and aborts — the code object can no longer be upgraded or self-initialized.

Note: I was not able to fully verify from the indexed code whether `LAZY_MODULE_INITIALIZATION` is currently enabled by default on Aptos mainnet (the relevant on-chain feature-flag source `types/src/on_chain_config/aptos_features.rs` could not be retrieved in full); this should be checked before treating the mainnet-relevance as confirmed rather than conditional on that flag being active.

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
