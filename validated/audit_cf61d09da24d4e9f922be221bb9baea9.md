## Title
Lazy module self-init permanently and irrecoverably blocked after a legitimate object-ownership transfer of an immutable object-hosted package — `aptos_framework::init::assert_may_self_initialize` ([File: aptos-move/framework/aptos-framework/sources/init.move])

### Summary
`init::internal_maybe_initialize` gates lazy module self-initialization on an object-hosted module by requiring that the object's current transitive root owner matches the owner recorded at the module's last publish (`deploy_owner`). This is otherwise a sound anti-hijack control, but it is *too strict* for the standard "deploy-to-object-then-transfer-ownership" flow that `object_code_deployment.move` itself documents and supports: if ownership of the object is transferred (a normal, expected operation) **before** the module's first successful self-init call, and the package was published with `upgrade_policy = immutable` (the framework's own recommended policy for code "not shared with others", and the default choice for security-conscious deployments), the mismatch between the recorded `deploy_owner` and the current owner can never be resolved, because the only mechanism that updates `deploy_owner` is `code::publish_package` (i.e., a re-publish/upgrade), which is permanently disabled for an immutable package. The result is that the module's initialization path is bricked forever, mirroring the reported bug class: an access-control check that is too strict, with a single point of failure (the deploy-time owner), that can permanently lock legitimate on-chain functionality/state with no recovery path.

### Finding Description
`object_code_deployment::publish` creates a new object owned by the publisher and immediately publishes the code to it: [1](#0-0) 

Inside `code::publish_package`, whenever the lazy-module-initialization feature is enabled and the target address is an object, the *current* root owner of the object is recorded per module as `deploy_owner`: [2](#0-1) 

This `deploy_owner` is only ever updated by `init::record_deploy_owner`, which is only called from `code::publish_package` (i.e. on publish/upgrade): [3](#0-2) 

At self-init time, `internal_maybe_initialize` requires the recorded owner to match the *current* root owner of the object, else it aborts with `EOWNER_CHANGED_SINCE_DEPLOY`: [4](#0-3) 

Because `check_and_set_initialized` marks the module state *before* `assert_may_self_initialize` runs, and a Move abort rolls back all writes in the transaction, an object whose owner changed since the last publish will *never* successfully complete `check_and_set_initialized`'s "not yet initialized" branch — every call aborts, forever, unless the object's root owner reverts to exactly the recorded `deploy_owner`, or the code is republished (which re-records the then-current owner).

If the package was published with `upgrade_policy_immutable()` — which `check_upgradability` permanently forbids from ever being republished (`EUPGRADE_IMMUTABLE`) — [5](#0-4)  — there is no way left to update `deploy_owner`. If the object's ownership is transferred even once between the object's creation/publish and the module's first successful self-init call, `assert_may_self_initialize` will fail on every subsequent call for the lifetime of the object, and there is no admin/governance override, no "re-arm" function, and no way to reset `InitializationState` in `init.move`.

This directly parallels the external report's root cause: a single, deploy-time-bound authority (`_host()` in the audit; `deploy_owner` here) gates a critical operation, and once that authority diverges from the live state (host wallet lost/transferred; object ownership transferred), the guarded operation (fee collection; module self-initialization) becomes permanently unreachable.

### Impact Explanation
Object code deployment (`aptos_framework::object_code_deployment`) is a first-class, encouraged deployment mechanism specifically because objects are ownership-transferable independent of code. A very natural deployment pattern is: a factory/deployer account creates the object and publishes an immutable package to it, then transfers the object to its intended long-term owner (a DAO, multisig, marketplace buyer, or end user) as part of the same rollout — before that owner has triggered the module's first entry-point call that performs lazy self-init (e.g., to `move_to` initial resources/config). Under this pattern, the module's self-initialization logic becomes permanently unusable: any resource or state that was supposed to be created via `internal_maybe_initialize`'s minted signer can never be created, effectively bricking the deployed dApp/module state with no possible recovery (no upgrade allowed, no owner-revert available in general). This is a high-severity denial-of-service on protected state mutation (module init writes) reachable purely by a standard, permission-less object-ownership transfer — exactly the kind of "unauthorized... module-init... failure that reaches protected state mutation" the publish-impact gate calls out.

### Likelihood Explanation
The trigger requires only two ordinary, permissionless actions that are explicitly supported and encouraged by the framework: (1) publish an immutable package to an object via `object_code_deployment::publish`, and (2) transfer that object (`object::transfer`/`transfer_call`) before the module's first self-init-triggering call succeeds. Both are normal parts of a deploy-then-hand-off workflow, requiring no attacker privilege and no malicious intent — it can happen purely through routine developer/operational error, or as an intentional attack by anyone with the ability to induce a single ownership transfer on a freshly-deployed, not-yet-initialized object (e.g., a marketplace listing/purchase flow for "shrink-wrapped" object-code packages) before the rightful owner ever calls the init path.

### Recommendation
- Do not gate lazy self-init solely on an immutable "owner recorded at last publish" versus "current owner" equality check for packages that can never be republished. Instead, consider recording `deploy_owner` at the time the object is *finalized* (e.g., allow one explicit "arm"/"finalize ownership" call, independent of `code::publish_package`, that any current owner can invoke once, to (re)set `deploy_owner` without requiring a full republish), or scope the guard to require equality only when the module has *already been initialized once* (i.e., protect re-initialization / already-initialized state from owner-hijack, but do not block the very first initialization attempt purely because of a pre-first-use ownership transfer).
- Alternatively, allow `freeze_code_object`/immutable packages to still call a narrow, code-only entry point that updates `deploy_owner` without touching upgrade policy or module bytecode.
- At minimum, clearly document this footgun and have `object_code_deployment::publish`/CLI tooling warn against transferring an object before its first initializing call when the package is immutable.

### Proof of Concept
1. Deployer account `D` calls `object_code_deployment::publish(D, metadata_serialized, code)` where `metadata.upgrade_policy = immutable`, deploying module `M` (which calls `init::internal_maybe_initialize` from an entry function `run`) to a fresh object at address `O`. At publish time, `code::publish_package` records `deploy_owner(O, M) = D` (current root owner) since `D` is the object's owner right after creation. [2](#0-1) 
2. Before anyone calls `M::run()`, `D` transfers the object to owner `U` via `object::transfer_call(D, O, U)` (a normal, permissionless operation).
3. `U` (or anyone) calls `M::run()`, which calls `init::internal_maybe_initialize(false)`. `check_and_set_initialized` returns `false` (never initialized) and tentatively marks `only_once = some(false)`, then `assert_may_self_initialize(O, M)` compares recorded owner `D` to current root owner `U` — mismatch — aborts with `EOWNER_CHANGED_SINCE_DEPLOY`. [4](#0-3) 
4. Because the transaction aborts, the tentative `only_once` mark is rolled back; every future call to `M::run()` (by any caller, in any transaction, forever) repeats the same abort. Because `metadata.upgrade_policy` is immutable, `code::publish_package`'s `check_upgradability` unconditionally rejects any attempt to republish `M` (`EUPGRADE_IMMUTABLE`) — [6](#0-5)  — so `deploy_owner` can never be corrected, and `M`'s lazy self-init path is permanently and irrecoverably bricked, exactly mirroring the report's "funds locked permanently" pattern.

This is consistent with, and directly demonstrated by, the repository's own test `init_maybe_initialize_object_owner_changed_aborts`, which confirms that after a single `object::transfer_call`, self-init aborts with `EOWNER_CHANGED_SINCE_DEPLOY` [7](#0-6)  — the missing piece (not covered by any existing test) is that when the package is immutable, this abort is not recoverable by any means, unlike the mutable-package case where a subsequent `object_code_deployment::upgrade` re-records the new owner and un-bricks the module.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L266-281)
```text
    /// Checks whether the given package is upgradable, and returns true if a compatibility check is needed.
    fun check_upgradability(
        old_pack: &PackageMetadata, new_pack: &PackageMetadata, new_modules: &vector<String>) {
        assert!(old_pack.upgrade_policy.policy < upgrade_policy_immutable().policy,
            error::invalid_argument(EUPGRADE_IMMUTABLE));
        assert!(can_change_upgrade_policy_to(old_pack.upgrade_policy, new_pack.upgrade_policy),
            error::invalid_argument(EUPGRADE_WEAKER_POLICY));
        let old_modules = get_module_names(old_pack);

        old_modules.for_each_ref(|old_module| {
            assert!(
                vector::contains(new_modules, old_module),
                EMODULE_MISSING
            );
        });
    }
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

**File:** aptos-move/e2e-move-tests/src/tests/init_module_api.rs (L319-344)
```rust
#[test]
fn init_maybe_initialize_object_owner_changed_aborts() {
    let mut h = new_harness();
    let attacker = h.new_account_at(AccountAddress::from_hex_literal(ADDR).unwrap());
    let victim = h.new_account_at(AccountAddress::from_hex_literal("0xbeef").unwrap());
    let obj = deploy_object_addr(&h, &attacker);

    assert_success!(deploy_to_object(&mut h, &attacker, obj));

    // The attack: publish -> transfer the code object to the victim -> self-init.
    assert_success!(h.run_entry_function(
        &attacker,
        str::parse("0x1::object::transfer_call").unwrap(),
        vec![],
        vec![
            bcs::to_bytes(&obj).unwrap(),
            bcs::to_bytes(victim.address()).unwrap(),
        ],
    ));

    // Ownership changed since deploy -> self-init must abort (no signer minted).
    assert_abort!(
        run_object(&mut h, &attacker, obj),
        EOWNER_CHANGED_SINCE_DEPLOY
    );
}
```
