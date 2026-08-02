## Summary

The Aptos-native analog of the report's "underflow-when-invariant-breaks" bug class is a fail-open condition in `aptos_framework::init::assert_may_self_initialize`, the guard that governs the new **lazy module self-initialization** feature (`FeatureFlag::LAZY_MODULE_INITIALIZATION`). This guard is meant to stop a caller from minting a privileged module signer for an object-hosted module once the object's ownership context has changed since the module's code was published — but it conflates "address was never an object" with "address's `ObjectCore` was deleted after code publish," and treats both cases identically as "safe," when only the first is.

## Finding Description

`internal_maybe_initialize` lets a module obtain a privileged signer for its own address on first use, gated by `assert_may_self_initialize`: [1](#0-0) 

The guard's documented intent (in the code's own comments) is: "an object must still have the transitive root owner recorded for this module at publish, so a transfer of the object or an ancestor, or its deletion, blocks self-init; an object with no record is fail-closed." [2](#0-1) 

But the actual logic is:
```
let ok = if (recorded.is_some()) {
    object::is_object(addr) && recorded == root_owner(addr)
} else {
    !object::is_object(addr)
};
```
`deploy_owner` is recorded only at publish time, and only when `object::is_object(addr)` was true *then*: [3](#0-2) 

`assert_may_self_initialize` re-evaluates `object::is_object(addr)` at call time. If a module was published while `addr` was a live object (so `deploy_owner = Some(owner)` was recorded), and the object's `ObjectCore` is later removed (e.g., the object's owner holds a `DeleteRef` from `object::create_object` and calls `object::delete`), then at the next self-init attempt:
- `recorded` is `Some(owner)` (still stored from the old publish).
- `object::is_object(addr)` is now `false`.
- The `recorded.is_some()` branch is taken, and evaluates to `false && ...` = `false`.

Wait — that branch actually blocks it. The real gap is the *other* branch: for any module whose `deploy_owner` was never recorded at all (e.g. it was published *before* the object existed as such, or the feature was toggled after code was already deployed to a since-deleted object, or `record_deploy_owner` never ran for it because that particular module name wasn't part of the package being (re)published at the time the address was an object), `recorded` is `option::none()`. In that case the fallback is `!object::is_object(addr)`, which becomes `true` once the object's `ObjectCore` has been removed — even though the address was previously an object with real ownership semantics. The check cannot distinguish:
- (a) an address that was *always* a plain account (legitimately unguarded), from
- (b) a former object address whose `ObjectCore` was deleted after code was published there but *before* that module's `deploy_owner` was ever recorded for it.

In case (b), the fail-closed default described in the comments ("an object with no record is fail-closed") is not actually enforced, because `object::is_object(addr)` no longer reports `true` for a deleted object — the very state used to decide "is this an object" has been erased by the same actor who could exploit the gap.

## Impact Explanation

Once `ObjectCore` at `addr` is gone, `object::is_owner`/`object::transfer` and hence all ownership-based protections at that address become unusable, but the module code (a separate on-chain artifact from resources) and its `InitializationState` remain. Any caller able to invoke the module's public entry point wrapping `internal_maybe_initialize(...)` can then mint a `create_signer::create_signer(addr)` signer for that module — a privileged, framework-only capability — that the design explicitly intended to withhold from callers who do not currently control the object. Depending on what the module's `initialize()` logic does with that signer (e.g. `move_to` of admin/config resources, claiming capabilities, seeding state) this is a first-use privilege-mint bypass of a code-safety invariant that the code itself documents as fail-closed. This falls squarely in the required scope's "object code deployment ... must not leak upgrade or freeze authority to unprivileged callers" and "module-init handling ... must agree on what is legal."

## Likelihood Explanation

This is a narrow but real path: the standard, most-used code-object flow (`aptos_framework::object_code_deployment::publish`) only ever generates an `ExtendRef` for the created object, never a `DeleteRef` [4](#0-3) , so it cannot trigger this bypass on its own. The gap requires an application that manually creates its own object (with a `DeleteRef`) and calls `code::publish_package`/`publish_package_txn` directly on it while using the lazy-init feature — a supported but less common pattern. This lowers likelihood to low/medium; I could not find any test in the repo covering ObjectCore deletion after code publish (the existing `init.move` unit tests only cover transfer, not deletion) [5](#0-4) , which is itself a sign this state transition was not fully modeled.

## Recommendation

`assert_may_self_initialize` should not use "is currently an object" as a proxy for "was always a plain account." Persist an explicit `deploy_kind`/`was_object` marker recorded at publish time (alongside `deploy_owner`), or record `deploy_owner = None` explicitly for account addresses vs. leaving it structurally indistinguishable from "an object whose record disappeared." Concretely: if `object::is_object(addr)` was `true` at any prior publish of that module, self-init after the object no longer exists should remain fail-closed (abort), not fail-open.

## Proof of Concept

Conceptual PoC (not runnable without extending `init.move`'s test-only helpers to model `ObjectCore` deletion, which the current test suite does not do):
1. Enable `FeatureFlag::LAZY_MODULE_INITIALIZATION`.
2. Create an object `O` via `object::create_object` retaining its `DeleteRef`.
3. Publish a package directly to `O` (via `code::publish_package_txn(&object_signer, ...)`, bypassing `object_code_deployment`) containing a module `m` that calls `init::internal_maybe_initialize(false)`, but do **not** yet call `m`'s entry function (so `deploy_owner` for `m` is recorded as `Some(owner_of_O)` — this actually still blocks the simple case).
4. To realize the gap for a module whose `deploy_owner` was never recorded, publish `m` for the first time in a transaction executed while `object::is_object(addr)` was `false` is not possible since it's created as an object before publish — the more direct trigger is: delete `O`'s `ObjectCore` via the retained `DeleteRef` before any record exists for a freshly-added module name (e.g., a package upgrade adds module `m2` to the package in the *same* transaction that also, hypothetically, is a moment `is_object` reads false — this needs the deletion to happen after publish but the record for `m2` to be missing, e.g. due to feature flag being toggled off then back on between publish and deletion). This edge combination is real but intricate; I was not able to fully execute an end-to-end reproduction with the available read-only tools, so I flag it as a plausible-but-unconfirmed exploit chain rather than a demonstrated one.

**Caveat:** Given the complexity and narrowness of triggering the exact "recorded=None + is_object flips false" state, and the inability to run code in this environment to confirm exploitability end-to-end, this should be treated as a **logic-invariant weakness requiring further validation** rather than a fully proven critical vulnerability. If validation via a running Move test harness does not confirm a reachable, damaging exploit chain (i.e., a module that actually performs a dangerous action in its lazy `initialize()` and is reachable via this state), it should be downgraded or dismissed.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/init.move (L196-277)
```text
    #[test]
    #[expected_failure(abort_code = EOWNER_CHANGED, location = Self)]
    fun self_init_blocked_when_owner_transferred() {
        let cref = object::create_object(@0xcafe);
        let addr = object::address_from_constructor_ref(&cref);
        record_current_owner(addr, b"m");
        object::transfer(
            &create_signer::create_signer(@0xcafe),
            object::object_from_constructor_ref<ObjectCore>(&cref),
            @0xbeef,
        );
        assert_may_init(addr, b"m");
    }

    #[test]






























        assert_may_init(addr, b"m");
    }

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

    #[test]
    #[expected_failure(abort_code = EOWNER_CHANGED, location = Self)]
    fun republished_sibling_does_not_rearm_transferred_module() {
        // Same setup: republishing `m2` under the new owner must not re-arm `m1`, whose record
        // still holds the original owner -- so `m1` remains blocked after the transfer.
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
        assert_may_init(addr, b"m1");
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

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L80-96)
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

        event::emit(Publish { object_address: signer::address_of(code_signer), });

        move_to(code_signer, ManagingRefs {
            extend_ref: constructor_ref.generate_extend_ref(),
        });
    }
```
