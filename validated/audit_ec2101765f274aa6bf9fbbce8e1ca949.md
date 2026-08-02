Based on my investigation, `init::reset_initialized` is defined in `init.move` but is never invoked anywhere in `code::publish_package` (or anywhere else in the framework I could find besides its own test module). This is a broken invariant in the module-init handling path, but I could not fully confirm the end-to-end severity (e.g., whether this breaks a code-safety/ownership guarantee vs. only an application-level re-init convenience feature) without seeing more of the surrounding `code.move` lines 210-241 (the file excerpt was truncated by the indexer) and without confirming there is no other native-layer call site that triggers `reset_initialized` (e.g., invoked directly from the VM/loader during upgrade rather than from Move code).

### Title
`init::reset_initialized` is dead code, so upgraded object/account modules never re-run lazy self-init - (File: aptos-move/framework/aptos-framework/sources/init.move)

### Summary
`aptos_framework::init` implements a lazy module self-initialization mechanism (`internal_maybe_initialize`) gated by the `LAZY_MODULE_INITIALIZATION` feature. Its own doc comments state that `only_once = some(false)` entries are supposed to be cleared on each module upgrade via `reset_initialized`, so the initializer re-runs after upgrade [1](#0-0) . However, `reset_initialized` is never called by `code::publish_package` (the sole entry point for module (re)publish/upgrade) or by any other production code path [2](#0-1) .

### Finding Description
`ModuleState.only_once` semantics are documented as: `none` before init, `some(false)` meaning "re-init after each upgrade (cleared by `reset_initialized`)", `some(true)` meaning "init only once" [1](#0-0) . The clearing function `reset_initialized` is explicitly documented as "Called on code upgrade to request re-run of initialization for the named module" [2](#0-1) .

`code::publish_package` is the single Move-level function that handles both first publish and subsequent upgrades of a package (via `check_upgradability`) [3](#0-2) . It does call `init::record_deploy_owner` per module for the object-ownership guard [4](#0-3) , but it never calls `init::reset_initialized`. A repo-wide search confirms `reset_initialized` appears only in `init.move` (definition + its own unit tests) and nowhere else in the framework, natives, or CLI code that would drive an upgrade flow.

Consequently, once a module has been initialized once (`check_and_set_initialized` sets `only_once = some(false)` on first call) [5](#0-4) , `internal_maybe_initialize` will return `option::none()` on every subsequent call forever, including after any code upgrade — even though the module's own doc/spec model this as a case that should re-arm on upgrade.

### Impact Explanation
This is primarily an availability/correctness break of the module-init contract rather than a direct fund-theft or ownership-hijack primitive as in the external report: modules that rely on `only_once=false` semantics to re-run migration/initialization logic after an upgrade will silently skip that logic forever after the first init. Depending on what a module's initializer does (e.g., re-registering resources, re-deriving signer capabilities, applying storage migrations expected on every upgrade), this could leave protected state permanently uninitialized/stale after a legitimate upgrade, which is a state-mutation-on-publish correctness failure. I could not verify a path where this alone grants an attacker unauthorized signer/ownership (the ownership guard `assert_may_self_initialize` via `record_deploy_owner`/`root_owner` appears independently sound based on the code reviewed), so I cannot claim "High/Critical" impact with certainty from local evidence alone — this needs confirmation against how `only_once=false` is actually intended to be used in shipped/planned framework or ecosystem modules.

### Likelihood Explanation
High likelihood of being reachable as soon as any module adopts `internal_maybe_initialize(false)` and is upgraded, since it requires no attacker action — it is a systemic correctness break in the framework's new lazy-init API. The feature (`LAZY_MODULE_INITIALIZATION`, flag 127) appears new/still gated off by default based on `types/src/on_chain_config/aptos_features.rs`, so real-world exposure depends on rollout status, which I could not fully confirm.

### Recommendation
Call `init::reset_initialized(addr, module_name)` for every module in the package inside `code::publish_package` when handling the upgrade branch (`index < len`), for modules that were previously registered, so `only_once = some(false)` entries are cleared and re-armed on each upgrade as documented. Add an e2e test (mirroring the existing `init_module_api.rs` tests) that publishes a module with `internal_maybe_initialize(false)`, calls the entry function once, upgrades the package, and asserts the initializer runs again.

### Proof of Concept
Conceptual reproduction using the existing test harness in `aptos-move/e2e-move-tests/src/tests/init_module_api.rs` [6](#0-5) :
1. Enable `LAZY_MODULE_INITIALIZATION` feature.
2. Publish module `0xcafe::test` using `make_module(false, "")`, which calls `init::internal_maybe_initialize(false)` and increments a `Counter` on init.
3. Call `run` once — `Counter.value == 1`.
4. Republish (upgrade) the same package with a compatible change.
5. Call `run` again — expected (per doc comments) that the initializer re-runs and `Counter.value == 2`; actual result is that `internal_maybe_initialize` still returns `option::none()` because `only_once` was never reset, so the initializer is skipped and `Counter.value` remains `1`.

I was not able to execute this against the live repo/CI in this session, so this PoC is derived analytically from the code paths cited above and should be verified by actually running it.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/init.move (L27-36)
```text
    /// Per-module initialization metadata.
    ///
    /// `only_once` is the flag first passed at initialization: `none` before init, `some(false)`
    /// re-inits after each upgrade (cleared by `reset_initialized`), `some(true)` inits only once.
    /// `deploy_owner` is the object's transitive root owner recorded at this module's last publish
    /// and gates object self-init (see `assert_may_self_initialize`); `none` for account addresses.
    struct ModuleState has store, copy, drop {
        only_once: Option<bool>,
        deploy_owner: Option<address>
    }
```

**File:** aptos-move/framework/aptos-framework/sources/init.move (L102-115)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/init.move (L124-136)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L188-204)
```text
        let package_immutable = &borrow_global<PackageRegistry>(addr).packages;
        let len = package_immutable.length();
        let index = len;
        let upgrade_number = 0;
        package_immutable.enumerate_ref(|i, old| {
            let old: &PackageMetadata = old;
            if (old.name == pack.name) {
                upgrade_number = old.upgrade_number + 1;
                check_upgradability(old, &pack, &module_names);
                index = i;
            } else {
                check_coexistence(old, &module_names)
            };
        });

        // Assign the upgrade counter.
        pack.upgrade_number = upgrade_number;
```

**File:** aptos-move/e2e-move-tests/src/tests/init_module_api.rs (L41-61)
```rust
fn make_module(only_once: bool, extra: &str) -> String {
    format!(
        r#"module 0xcafe::test {{
            use aptos_framework::init;
            struct Counter has key {{ value: u64 }}
            public entry fun run(_s: &signer) {{
                let s = init::internal_maybe_initialize({only_once});
                if (s.is_some()) {{
                    initialize(&s.destroy_some());
                }}
            }}
            fun initialize(s: &signer) {{
                if (exists<Counter>(@0xcafe)) {{
                    Counter[@0xcafe].value += 1;
                }} else {{
                    move_to(s, Counter {{ value: 1 }});
                }}
            }}
            {extra}
        }}"#
    )
```
