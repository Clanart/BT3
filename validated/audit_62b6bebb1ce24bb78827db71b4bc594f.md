### Title
Lazy module self-init owner gate uses point-in-time equality instead of a "never changed" invariant, letting a transient object-owner window survive undetected - ([File: aptos-move/framework/aptos-framework/sources/init.move])

### Summary
`init::assert_may_self_initialize` gates lazy `init_module` execution for object-hosted modules by comparing the object's *current* root owner to the owner recorded at last publish. Like the reported balance-based nonce (which only checks a mutable value at call time rather than tracking a monotonic one-time-use condition), this check only asks "does the owner right now equal the owner then?" It does not detect an owner round-trip (original owner → transient owner → back to original owner) that occurred in between. During that transient window, the temporary owner is a legitimate `object::is_owner` holder and can call owner-gated APIs — most notably `object_code_deployment::get_code_object_signer`, which mints a real signer for the code object's address — to plant/alter state under that address. After the object is handed back, `assert_may_self_initialize` reports "no change" and lazy self-init proceeds as if the object had been continuously controlled by the trusted owner the whole time.

### Finding Description
`init.move` records, per module, the object's transitive root owner at publish time (`record_deploy_owner`, called from `code::publish_package` at [1](#0-0) ), and later gates lazy self-initialization with: [2](#0-1) 

The check only compares two point-in-time values — `recorded` (owner at last publish) and the object's owner **right now** — exactly like the reported bug's `currentMakerBalance < order.nonceBalance` check that only looks at balance at call time. It has no way to represent "the owner was different at some point between publish and now." Consequently:

1. Original owner `O` publishes/owns object `addr`; `deploy_owner = O` is recorded ( [3](#0-2) ).
2. `O` transfers `addr` to `A` (e.g. as part of an atomic marketplace/escrow/flash-ownership pattern using `object::transfer`, which only requires the *current* owner's signature — no cooldown or history is tracked by `object.move` either).
3. While owning `addr`, `A` calls `object_code_deployment::get_code_object_signer`, which succeeds because it only checks current ownership: [4](#0-3) . This hands `A` a real `signer` for `addr`, letting `A` call `move_to<T>(&signer, ...)` or invoke any function requiring that signer, injecting attacker-controlled resources/state at `addr`.
4. `A` transfers `addr` back to `O` (same or a later transaction).
5. Later, when some entry function triggers `init::internal_maybe_initialize`, `assert_may_self_initialize` sees `recorded(O) == current(O)` and allows self-init to proceed under the false premise that ownership "never changed since deploy" — even though `A` had a genuine ownership window and used it to mutate state at that address.

This breaks the code-safety invariant the whole mechanism exists to enforce (see the doc comment: "a transfer of the object or an ancestor... blocks self-init"), because the guard is defined as equality-of-current-value rather than "no change occurred, ever." Note that if `A` instead used the ownership window to *republish* a malicious module via `object_code_deployment::upgrade`, that republish itself calls `record_deploy_owner` with `A` as the current owner at that moment ( [5](#0-4) ), so a subsequent transfer back to `O` would correctly re-trigger the mismatch and block self-init — that particular sub-path is safe. The exposed gap is specifically the "no republish, just borrow ownership to call other owner-gated APIs" path, of which `get_code_object_signer` is the concrete, exploitable instance in the current codebase.

### Impact Explanation
This is a code-safety / module-init validation gap directly in the publish path (`code::publish_package` → `init::record_deploy_owner`, and `object_code_deployment::upgrade`/`get_code_object_signer`, all gated by object ownership). A transient, attacker-obtained ownership window (achievable via any legitimate protocol pattern that temporarily transfers object ownership, e.g., marketplace listing/escrow, atomic swap, or "flash ownership" composition) lets an unprivileged party obtain a genuine signer for the code object's address and mutate resources stored there, while the framework's own "owner changed since deploy" protection — whose entire purpose is to prevent exactly this class of interference — silently reports no issue once ownership is returned. Any module relying on `internal_maybe_initialize`'s owner-continuity guarantee to trust the state at its own address is misled, which can lead to state corruption or unauthorized manipulation of storage under a code object address that the framework advertises as protected.

### Likelihood Explanation
Likelihood is moderate: it requires a workflow where an object (particularly one deployed via `object_code_deployment`) is temporarily transferred to an untrusted party and later returned — a realistic pattern for marketplaces, escrows, and DeFi composability (e.g., NFT/object "flash loan" or listing flows) that are common on Aptos. No governance or privileged role is needed; only ordinary owner-transfer capability, which is permissionless by design (`object::transfer` is callable by any current owner).

### Recommendation
Replace the point-in-time equality check with a monotonic invariant: track whether the owner has *ever* deviated from the recorded deploy owner since publish (e.g., a boolean/flag flipped by a `transfer_hook`/`TransferEvent`-driven mechanism, or by consulting an owner-history/generation counter on `ObjectCore` rather than only the current root owner), so that any transient change — not just a currently-visible mismatch — permanently invalidates self-init eligibility until the module is republished. At minimum, `object_code_deployment::get_code_object_signer` (and any other owner-gated privileged accessor) should not be reachable by a transient owner without also invalidating the recorded `deploy_owner` state for all modules at that address, mirroring what `record_deploy_owner`/upgrade already does.

### Proof of Concept
Conceptual reproduction using the existing test harness in `init.move`:
1. `O` creates object `addr`, publishes a lazily-initialized module, `record_deploy_owner(addr, "m", O)` runs.
2. `O` transfers `addr` to `A` via `object::transfer`.
3. `A` calls `object_code_deployment::get_code_object_signer(A, code_object)` (succeeds, `object::is_owner(code_object, A)` is true) and uses the returned signer to `move_to` attacker-chosen data at `addr`.
4. `A` transfers `addr` back to `O`.
5. A later call into the module triggers `init::internal_maybe_initialize(false)`; `assert_may_self_initialize` computes `recorded(O) == current_owner(O)` → passes, and lazy init proceeds, unaware that step 3 occurred. This is directly analogous to the existing unit test `republished_module_allowed_under_new_owner` ( [6](#0-5) ), except no republish occurs — only an owner round-trip plus an owner-gated signer grab in between, which the current test suite does not cover.

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

**File:** aptos-move/framework/aptos-framework/sources/init.move (L93-100)
```text
    /// Records `owner` as the object root owner of the module named `module_name` at (re)publish, to
    /// gate its later self-init (see `assert_may_self_initialize`). Called per module by
    /// `code::publish_package` for object addresses only.
    package fun record_deploy_owner(addr: address, module_name: vector<u8>, owner: address) {
        let module_id = module_id_from_name(module_name);
        ensure_module_state(addr, module_id);
        InitializationState[addr].modules.borrow_mut(&module_id).deploy_owner = option::some(owner);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/init.move (L244-259)
```text
    #[test]
    fun republished_module_allowed_under_new_owner() {
        // Two modules published under @0xcafe; object transferred; only `m2` republished under the
        // new owner. `m2`'s record now matches the current owner, so it may self-init.
        let cref = object::create_object(@0xcafe);
        let addr = object::address_from_constructor_ref(&cref);
        record_current_owner(addr, b"m1");
        record_current_owner(addr, b"m2");
        object::transfer(
            &create_signer::create_signer(@0xcafe),
            object::object_from_constructor_ref<ObjectCore>(&cref),
            @0xbeef,
        );
        record_current_owner(addr, b"m2");
        assert_may_init(addr, b"m2");
    }
```

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L149-161)
```text
    public fun get_code_object_signer(publisher: &signer, code_object: Object<PackageRegistry>): signer {
        let publisher_address = signer::address_of(publisher);
        assert!(
            object::is_owner(code_object, publisher_address),
            error::permission_denied(ENOT_CODE_OBJECT_OWNER),
        );

        let code_object_address = code_object.object_address();
        assert!(exists<ManagingRefs>(code_object_address), error::not_found(ECODE_OBJECT_DOES_NOT_EXIST));

        let extend_ref = &borrow_global<ManagingRefs>(code_object_address).extend_ref;
        extend_ref.generate_signer_for_extending()
    }
```
