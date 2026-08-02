## Finding

### Title
Module initialization state uses a 128-bit truncated SHA3-256 hash as `ModuleId`, allowing name-collision aliasing of code-object ownership/init records - (File: `aptos-move/framework/aptos-framework/sources/init.move`)

### Summary
Aptos's lazy module-initialization system (used to gate self-initializing code deployed via `object_code_deployment`) identifies modules not by their full name but by a `ModuleId` derived from only the first **16 bytes (128 bits)** of `sha3_256(module_name)`. This truncated hash is used as the map key for `ModuleState { only_once, deploy_owner }`, the exact state that `init::assert_may_self_initialize` relies on to prevent a stale/republished module from self-initializing after the hosting object has changed owners. This mirrors the C4 report's bug class of "only part of a hash used as an identifier, enabling collision attacks" - here applied to a security-critical ownership-tracking key rather than a governance/distribution struct hash.

### Finding Description
`module_id_from_name` computes the module identity as: [1](#0-0) 

and the native side independently reproduces the same truncation: [2](#0-1) 

This `ModuleId` (a bare `u128`) is used as the sole key into `InitializationState.modules: OrderedMap<ModuleId, ModuleState>`: [3](#0-2) 

`ModuleState.deploy_owner` records, per module, the object's transitive root owner at the module's last publish, and is the value `assert_may_self_initialize` checks against the object's *current* root owner before minting a privileged signer for that module: [4](#0-3) 

`code::publish_package` writes this per-module record using only the module *name* (hashed down to the same 128-bit `ModuleId`) whenever a module is (re)published to an object address: [5](#0-4) 

Because the map key is a 128-bit hash rather than the full module name, two distinct module names `M1` and `M2` published (at any time) under the same address whose `sha3_256(name)[0..16]` values collide will **alias to the same `ModuleState` entry**. `record_deploy_owner`, `reset_initialized`, and `check_and_set_initialized` all key exclusively off this truncated hash: [6](#0-5) 

An attacker who fully controls a package's module names (as any publisher does) can search offline for a name `M2` colliding with an existing module `M1`'s `ModuleId`, then use ordinary publish/upgrade calls (`code::publish_package_txn`, `object_code_deployment::publish`/`upgrade`) to overwrite or read `M1`'s recorded `deploy_owner`/`only_once` state via `M2`, and vice versa - defeating the very invariant `assert_may_self_initialize` is designed to enforce: that self-initialization (minting a privileged `signer` through `internal_maybe_initialize`) is only permitted if the object's root owner is unchanged since the aliased module's last legitimate publish.

### Impact Explanation
If an attacker finds a collision, they can:
- Overwrite the `deploy_owner` recorded for a sibling module at the same object address with an owner value of their choosing while they still control the object, then transfer the object away and have the *other*, aliased module still pass the "owner unchanged" check - bypassing `EOWNER_CHANGED_SINCE_DEPLOY` and obtaining a privileged self-init `signer` for code effectively controlled by a different, now-unauthorized party.
- Alias/short-circuit the `only_once` flag between two modules, causing a module intended to run its initializer exactly once (or re-run on every upgrade) to instead inherit the wrong state from a colliding sibling, producing incorrect privileged initialization behavior.

This falls squarely in the required impact category of "unauthorized...code-object ownership change" bypass reaching protected state mutation (a `move_to`-capable signer being granted despite failed ownership continuity).

### Likelihood Explanation
Exploitation requires finding a preimage collision within a 128-bit truncated SHA3-256 space, i.e. ~2^64 hash evaluations by the birthday bound, restricted to the (large but constrained) charset of valid Move module identifiers. This is the same order of magnitude the original external report itself judged for the 15/16-byte (120–128 bit) truncated hashes in `TokenDistributor`/`Crowdfund` - which the project still treated as a confirmed, real weakness worth fixing (extended to full 32 bytes) despite acknowledging it as currently impractical for an average attacker and only plausible for a well-resourced adversary against high-value targets. The same caveat applies here: not practically exploitable by commodity hardware today, but it is a genuine, avoidable weakening of a security-relevant identifier versus using the full 256-bit digest (or the full module name) as the key.

### Recommendation
Key `InitializationState.modules` by the full module name (or the full 32-byte `sha3_256` digest) instead of a 16-byte truncated hash, eliminating the collision space entirely. If a fixed-size key is required for gas/storage reasons, use the full 256-bit hash rather than truncating to 128 bits, consistent with the standard mitigation accepted in the original report ("use the standard, 32-bytes, output of `keccak256()`/`sha3_256()`").

### Proof of Concept
Conceptual (no working collision was computed, since finding one requires ~2^64 offline SHA3-256 evaluations, which is outside the scope of this analysis):
1. Object `O` is created and module `M1` is published to it via `object_code_deployment::publish`; `init::record_deploy_owner(O, b"M1", owner_A)` stores `deploy_owner = owner_A` keyed by `ModuleId = sha3_256(b"M1")[0..16]`.
2. Offline, the attacker searches for a valid Move identifier `M2` such that `sha3_256(b"M2")[0..16] == sha3_256(b"M1")[0..16]`.
3. The attacker republishes/upgrades object `O` to include module `M2` (under owner `owner_A`, which they still control), causing `record_deploy_owner(O, b"M2", owner_A)` to write into the *same* `ModuleState` entry as `M1` (`init.move:96-100`).
4. The attacker transfers object `O`'s ownership to `owner_B`.
5. Module `M1`'s `init_module`-driven `internal_maybe_initialize` call now reads the `ModuleState` entry that was last written via `M2`'s publish, and depending on write order this can make `assert_may_self_initialize` (`init.move:74-83`) incorrectly treat the "owner at last publish" as still matching, when in fact `M1`'s own actual publish/ownership history should have blocked it.

Because I could not compute an actual 128-bit collision, I cannot demonstrate an end-to-end working exploit transaction; this PoC therefore documents the exact code path and required precondition (a name collision) rather than a runnable proof.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/init.move (L33-44)
```text
    struct ModuleState has store, copy, drop {
        only_once: Option<bool>,
        deploy_owner: Option<address>
    }

    /// Per-address initialization metadata, keyed by module so each module is gated on the owner at
    /// its own last publish -- republishing one module does not re-arm a sibling.
    enum InitializationState has key {
        V1 {
            modules: OrderedMap<ModuleId, ModuleState>
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/init.move (L70-91)
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

    /// The object root owner recorded for `module_id` at its last publish, or `none` if the module
    /// has no such record (an account module, or an object module never recorded).
    fun recorded_deploy_owner(addr: address, module_id: ModuleId): Option<address> {
        if (!exists<InitializationState>(addr)) return option::none();
        let modules = &InitializationState[addr].modules;
        if (modules.contains(&module_id)) modules.borrow(&module_id).deploy_owner else option::none()
    }
```

**File:** aptos-move/framework/aptos-framework/sources/init.move (L93-136)
```text
    /// Records `owner` as the object root owner of the module named `module_name` at (re)publish, to
    /// gate its later self-init (see `assert_may_self_initialize`). Called per module by
    /// `code::publish_package` for object addresses only.
    package fun record_deploy_owner(addr: address, module_name: vector<u8>, owner: address) {
        let module_id = module_id_from_name(module_name);
        ensure_module_state(addr, module_id);
        InitializationState[addr].modules.borrow_mut(&module_id).deploy_owner = option::some(owner);
    }

    /// Called on code upgrade to request re-run of initialization for the named module. Skipped for
    /// modules that used `only_once = true` when first initialized. Keeps the recorded deploy owner.
    package fun reset_initialized(addr: address, module_name: vector<u8>) {
        if (exists<InitializationState>(addr)) {
            let modules = &mut InitializationState[addr].modules;
            let module_id = module_id_from_name(module_name);
            if (modules.contains(&module_id)) {
                let state = modules.borrow_mut(&module_id);
                if (state.only_once == option::some(false)) {
                    state.only_once = option::none();
                }
            }
        }
    }

    /// Creates the id for a module name (see `get_caller_address_and_module_id` for the format).
    fun module_id_from_name(name: vector<u8>): ModuleId {
        let hash = hash::sha3_256(name);
        hash.trim(16);
        ModuleId { hash: from_bcs::to_u128(hash) }
    }

    /// Returns true if the module is already initialized. Otherwise marks it initialized, recording
    /// `only_once`: if true the entry survives upgrades (initializer never re-runs); if false an
    /// upgrade resets it (initializer re-runs).
    fun check_and_set_initialized(addr: address, module_id: ModuleId, only_once: bool): bool {
        ensure_module_state(addr, module_id);
        let state = InitializationState[addr].modules.borrow_mut(&module_id);
        if (state.only_once.is_some()) {
            true
        } else {
            state.only_once = option::some(only_once);
            false
        }
    }
```

**File:** aptos-move/framework/natives/src/init.rs (L62-69)
```rust
    // Must produce the same value as `init::module_id_from_name`: the sha3-256 of the
    // module name bytes, trimmed to 16 bytes, read as a (BCS) little-endian u128.
    let hash = sha3::Sha3_256::digest(name_bytes);
    let module_id_hash = u128::from_le_bytes(
        hash[..16]
            .try_into()
            .expect("sha3-256 digest has at least 16 bytes"),
    );
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
