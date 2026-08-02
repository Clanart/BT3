## Analysis Summary

The external report's core lesson — an "owner"/authority check on a code‑management primitive can silently regress and let an unprivileged party obtain control it shouldn't have — maps in Aptos to the `aptos_framework::init` module's object‑ownership guard for lazy module self‑initialization [1](#0-0) , which mints a raw `signer` for a module's own address and is exactly the kind of "publish/init reaches protected state mutation" surface called out by the Publish Impact Gate.

### Title
Object self-initialization guard fails open once the hosting object is deleted (`is_object(addr)` used as the sole proxy for "no record needed") - (File: `aptos-move/framework/aptos-framework/sources/init.move`)

### Summary
`init::assert_may_self_initialize` is meant to be fail-closed: an object-hosted module may only self-initialize (mint its own `signer`) if the object's current root owner still matches the owner recorded at the module's last publish; if no owner was ever recorded for an object address, the code is supposed to treat that as untrusted and block. The "no record" branch, however, distinguishes "object needing a record" from "plain account, no record needed" purely by calling `object::is_object(addr)` at check time. If the object at `addr` is later deleted (so `ObjectCore` no longer exists there) while no `deploy_owner` was ever recorded, `is_object(addr)` becomes `false`, and the guard treats the address as if it were a normal never-object account — permitting self-initialization and signer minting with no authorization check at all.

### Finding Description
`internal_maybe_initialize` calls `assert_may_self_initialize(addr, module_id)` before minting a signer for `addr`: [2](#0-1) 

The guard logic is: [3](#0-2) 

```
let ok = if (recorded.is_some()) {
    object::is_object(addr) && recorded.destroy_some() == root_owner()
} else {
    !object::is_object(addr)
};
```

For the "no record" branch, safety hinges entirely on `object::is_object(addr)` still being `true` at check time — i.e. it assumes an object address with no record is always a *live* object that simply predates the feature or was published while the feature flag was off (both are fail-closed, confirmed by the `self_init_blocked_when_object_has_no_recorded_owner` test). That assumption breaks once the object is deleted: `ObjectCore` is removed, `is_object(addr)` flips to `false`, and the "no record" branch now evaluates to `true` (self-init allowed), identical to how a legitimate never-an-object account address is treated.

The record is only ever written from `code::publish_package`, and only when the lazy-init feature was already enabled at publish time: [4](#0-3) 

So a module hosted at an object address can legitimately have `deploy_owner = none` if it was published (or last republished) while `features::is_lazy_module_initialization_enabled()` was `false` — this is not a corner case requiring attacker-controlled state corruption, it's the ordinary state for any package published before that feature was turned on, or any object-hosted package whose owner never triggered a record.

The only test coverage for the "no record" branch keeps the object alive: [5](#0-4) 
There is no test exercising "no record + object subsequently deleted", which is the exact gap.

### Impact Explanation
If this condition is reached, any caller able to invoke the module's entry function that calls `init::internal_maybe_initialize` obtains a `signer::create_signer(addr)` for `addr` — a `signer` for the (formerly) object address — with the object-ownership check completely bypassed. This is a state-mutation authority (Move's most privileged capability: an arbitrary `signer`) minted without any owner check, at an address that the design explicitly intends to gate on current ownership. That matches the Publish Gate's "unauthorized... code-object ownership change" / "verifier or module-init... failure that reaches protected state mutation" category, and could be used by anyone (not the current/former owner) to run privileged one-time initializer code (`move_to`, capability creation, etc.) at that address once the guard is bypassed.

### Likelihood Explanation
Exploitability depends on being able to get a `deploy_owner = none` object-hosted module and then delete that object:
- The `deploy_owner = none` precondition is common: any package published under `code::publish_package`/`object_code_deployment::publish` before `LAZY_MODULE_INITIALIZATION` was enabled network-wide, or any object-hosted package whose module never went through a re-publish after the feature was enabled, has no record.
- Deletion requires the object to have been created with a `DeleteRef`. The standard `object_code_deployment::publish` path uses `object::create_named_object`, and I was not able to fully verify in this session whether named objects created that way can generate a `DeleteRef` (I could not confirm `can_generate_delete_ref` semantics for `create_named_object` before running out of tool calls). If named code-deployment objects are non-deletable by construction, this specific path is not reachable through the standard object-code-deployment flow. However, `code::publish_package_txn` is a general public entry point — any Move package can create its own deletable object (via `object::create_object`, which does support `generate_delete_ref`) and publish a module to that object's address directly, independent of the `object_code_deployment` module, satisfying both preconditions.

Given this uncertainty about deletability for the standard object-code-deployment path, likelihood should be treated as **medium** rather than confirmed-high without further verification of `object::create_named_object`'s `can_generate_delete_ref` behavior.

### Recommendation
- Do not rely on `object::is_object(addr)` alone to distinguish "never was an object" from "was an object, is now deleted." Persist an explicit boolean (e.g. `was_object: bool`) in `ModuleState` at publish time, or refuse to allow self-initialization at all once `is_object(addr)` is `false` after having previously observed the address as an object.
- Alternatively, always record a `deploy_owner` for object-hosted packages regardless of the `is_lazy_module_initialization_enabled()` feature flag state, and treat "no record" strictly as "this address was never an object," independent of its current live/deleted status.
- Add an explicit test: publish an object-hosted module with no recorded owner, delete the underlying object (where deletable), and assert `assert_may_self_initialize` still aborts with `EOWNER_CHANGED_SINCE_DEPLOY`.

### Proof of Concept
Conceptual repro (subject to confirming a deletable object path):
1. Create an object via `object::create_object(creator)`, obtaining a `ConstructorRef` with a `DeleteRef`.
2. Using the constructor's generated signer, call `code::publish_package_txn` to publish a module `M` at the object's address while `features::is_lazy_module_initialization_enabled()` is `false` (or before the feature exists on-chain) — no `deploy_owner` gets recorded for `M`.
3. Turn on `LAZY_MODULE_INITIALIZATION` (or it's already on network-wide but `M`'s owner never re-published to record itself).
4. Delete the object via the held `DeleteRef` (`object::delete`), removing `ObjectCore` while `M`'s code remains at that address.
5. Any account calls `M`'s entry function that invokes `init::internal_maybe_initialize(...)`. `recorded_deploy_owner` returns `none`; `object::is_object(addr)` is now `false`; the guard evaluates `!false = true`, and a `signer` for the deleted object's address is minted with no ownership check, running `M`'s initializer under attacker-triggered control.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/init.move (L54-68)
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

**File:** aptos-move/framework/aptos-framework/sources/init.move (L236-242)
```text
    #[test]
    #[expected_failure(abort_code = EOWNER_CHANGED, location = Self)]
    fun self_init_blocked_when_object_has_no_recorded_owner() {
        // Fail-closed: an object whose owner was not recorded at publish cannot self-init.
        let addr = object::address_from_constructor_ref(&object::create_object(@0xcafe));
        assert_may_init(addr, b"m");
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
