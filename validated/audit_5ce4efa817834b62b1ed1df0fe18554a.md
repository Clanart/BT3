Given the iteration limit, I'll present the strongest verified local candidate along with an explicit caveat about what remains unconfirmed.

### Title
Dependency-check bypass in `code::publish_package` lets a package hide a real bytecode dependency on a lower-`upgrade_policy` package, defeating the allowed-dependency/upgrade-policy invariant - (File: `aptos-move/framework/aptos-framework/sources/code.move`)

### Summary
`code::publish_package` computes the set of allowed module dependencies (`allowed_deps`) purely from the **declared** `PackageMetadata.deps` field, and only forwards that list to the native bytecode-dependency checker (`request_publish_with_allowed_deps`) when the `CODE_DEPENDENCY_CHECK` feature flag is enabled. When it is not enabled, the code falls back to `request_publish`, which performs no cross-check between the declared dependency metadata and the actual immediate module dependencies embedded in the compiled bytecode. [1](#0-0) 

### Finding Description
`check_dependencies` builds `allowed_module_deps` strictly from `pack.deps` (the caller-supplied `PackageMetadata`), enforcing that each declared dependency's `upgrade_policy` is at least as strict as the publishing package's policy (`EDEP_WEAKER_POLICY`). [2](#0-1) 

This is exactly the mechanism meant to stop a package from claiming an `immutable`/`compatible` policy while actually depending on a weaker, upgradeable module (the on-chain analog of the report's "trusted invariant that is silently violated" pattern: instead of a killed Curve pool blocking `remove_liquidity_one_coin`, here a metadata/bytecode mismatch silently defeats an upgrade-safety guarantee). The invariant is only enforced end-to-end (against the *actual compiled bytecode*, not just the caller-supplied metadata) when `features::code_dependency_check_enabled()` is true; otherwise `request_publish` is invoked, which has no `allowed_deps` parameter at all. [3](#0-2) 

The repository's own e2e test explicitly demonstrates this: a package `pack2` declares `UpgradePolicy::immutable()` but its module actually calls into `pack1` (policy `compatible`). The test patches the serialized metadata to `metadata.deps.clear()` (hiding the dependency from `PackageMetadata` while the compiled module still references it). With `CODE_DEPENDENCY_CHECK` disabled, publishing **succeeds** — the test comment states outright "In the previous version we were not able to detect this problem." [4](#0-3) 

### Impact Explanation
If reached with the flag disabled, this allows publishing a package that is nominally `immutable` (or otherwise strict-policy) while its bytecode actually links against a package with a weaker/upgradeable policy at the same or another address. Downstream consumers and auditors trust the declared `upgrade_policy`/`deps` metadata as an authoritative description of what code is committed and callable; a mismatch lets the "immutable" package's effective behavior change later via an upgrade of the hidden dependency, which is precisely the "mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes" class called out in the task's impact gate. This is a code-safety/ownership-adjacent bypass with high potential impact (silently mutable "immutable" code) but only if this bypass path is reachable in current network configuration.

### Likelihood Explanation
This is where I could not close the loop given the remaining iteration budget: I was unable to confirm whether `FeatureFlag::CODE_DEPENDENCY_CHECK` is enabled by default on Aptos mainnet today. The flag exists in `aptos_features.rs` and `aptos-release-builder/src/components/feature_flags.rs`, and the fact that a dedicated feature flag was introduced (rather than removing the old `request_publish` path outright) strongly suggests this was a previously real, exploitable bug that has since been mitigated by defaulting the flag on. I could not verify the current default/on-chain value of this flag in the time available, so I cannot assert current mainnet relevance with certainty — this must be confirmed (e.g., via `on_chain_config`/genesis feature defaults or a live `aptos_framework::features` query) before treating this as an actionable, presently-exploitable finding.

### Recommendation
- Verify whether `CODE_DEPENDENCY_CHECK` is enabled by default in current mainnet genesis/feature configuration.
- If it can still be disabled (via governance or on networks that haven't enabled it), remove the legacy `request_publish` (no-allowed-deps) fallback entirely, or make `check_dependencies`/allowed-deps verification unconditional and independent of any feature flag, so the metadata-to-bytecode dependency check can never be bypassed.
- Add an invariant check that the modules' actual (compiled) immediate dependencies are a subset of `pack.deps` regardless of feature-flag state, at both the Move and native verification layers.

### Proof of Concept
The existing repository test is itself the PoC for the underlying mechanism (with `CODE_DEPENDENCY_CHECK` disabled): [5](#0-4) 

Since I could not confirm the flag's current default state, I am not able to assert with confidence that this is exploitable against present-day mainnet — this should be validated as a first step before further action.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/code.move (L224-230)
```text
        // Request publish
        if (features::code_dependency_check_enabled())
            request_publish_with_allowed_deps(addr, module_names, allowed_deps, code, policy.policy)
        else
        // The new `request_publish_with_allowed_deps` has not yet rolled out, so call downwards
        // compatible code.
            request_publish(addr, module_names, code, policy.policy)
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L300-345)
```text
    fun check_dependencies(publish_address: address, pack: &PackageMetadata): vector<AllowedDep>
    acquires PackageRegistry {
        let allowed_module_deps = vector::empty();
        let deps = &pack.deps;
        deps.for_each_ref(|dep| {
            let dep: &PackageDep = dep;
            assert!(exists<PackageRegistry>(dep.account), error::not_found(EPACKAGE_DEP_MISSING));
            if (is_policy_exempted_address(dep.account)) {
                // Allow all modules from this address, by using "" as a wildcard in the AllowedDep
                let account: address = dep.account;
                let module_name = string::utf8(b"");
                vector::push_back(&mut allowed_module_deps, AllowedDep { account, module_name });
            } else {
                let registry = borrow_global<PackageRegistry>(dep.account);
                let found = vector::any(&registry.packages, |dep_pack| {
                    let dep_pack: &PackageMetadata = dep_pack;
                    if (dep_pack.name == dep.package_name) {
                        // Check policy
                        assert!(
                            dep_pack.upgrade_policy.policy >= pack.upgrade_policy.policy,
                            error::invalid_argument(EDEP_WEAKER_POLICY)
                        );
                        if (dep_pack.upgrade_policy == upgrade_policy_arbitrary()) {
                            assert!(
                                dep.account == publish_address,
                                error::invalid_argument(EDEP_ARBITRARY_NOT_SAME_ADDRESS)
                            )
                        };
                        // Add allowed deps
                        let account = dep.account;
                        let k = 0;
                        let r = vector::length(&dep_pack.modules);
                        while (k < r) {
                            let module_name = vector::borrow(&dep_pack.modules, k).name;
                            vector::push_back(&mut allowed_module_deps, AllowedDep { account, module_name });
                            k += 1;
                        };
                        true
                    } else {
                        false
                    }
                });
                assert!(found, error::not_found(EPACKAGE_DEP_MISSING));
            };
        });
        allowed_module_deps
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L366-391)
```text
    /// Native function to initiate module loading
    native fun request_publish(
        owner: address,
        expected_modules: vector<String>,
        bundle: vector<vector<u8>>,
        policy: u8
    );

    /// A helper type for request_publish_with_allowed_deps
    struct AllowedDep has drop {
        /// Address of the module.
        account: address,
        /// Name of the module. If this is the empty string, then this serves as a wildcard for
        /// all modules from this address. This is used for speeding up dependency checking for packages from
        /// well-known framework addresses, where we can assume that there are no malicious packages.
        module_name: String
    }

    /// Native function to initiate module loading, including a list of allowed dependencies.
    native fun request_publish_with_allowed_deps(
        owner: address,
        expected_modules: vector<String>,
        allowed_deps: vector<AllowedDep>,
        bundle: vector<vector<u8>>,
        policy: u8
    );
```

**File:** aptos-move/e2e-move-tests/src/tests/code_publishing.rs (L361-394)
```rust
#[rstest(enabled, disabled,
         case(vec![], vec![FeatureFlag::CODE_DEPENDENCY_CHECK]),
         case(vec![FeatureFlag::CODE_DEPENDENCY_CHECK], vec![]),
)]
fn code_publishing_faked_dependency(enabled: Vec<FeatureFlag>, disabled: Vec<FeatureFlag>) {
    let mut h = MoveHarness::new_with_features(enabled.clone(), disabled);
    let acc1 = h.new_account_at(AccountAddress::from_hex_literal("0xcafe").unwrap());
    let acc2 = h.new_account_at(AccountAddress::from_hex_literal("0xdeaf").unwrap());

    let mut pack1 = PackageBuilder::new("Package1").with_policy(UpgradePolicy::compat());
    pack1.add_source("m", "module 0xcafe::m { public fun f() {} }");
    let pack1_dir = pack1.write_to_temp().unwrap();
    assert_success!(h.publish_package(&acc1, pack1_dir.path()));

    // pack2 has a higher policy and should not be able to depend on pack1
    let mut pack2 = PackageBuilder::new("Package2").with_policy(UpgradePolicy::immutable());
    pack2.add_local_dep("Package1", &pack1_dir.path().to_string_lossy());
    pack2.add_source(
        "m",
        "module 0xdeaf::m { use 0xcafe::m; public fun f() { m::f() } }",
    );
    let pack2_dir = pack2.write_to_temp().unwrap();
    let result = h.publish_package_with_patcher(&acc2, pack2_dir.path(), |metadata| {
        // Hide the dependency from the lower policy package from the metadata. We detect this
        // this via checking the actual bytecode module dependencies.
        metadata.deps.clear()
    });
    if !enabled.contains(&FeatureFlag::CODE_DEPENDENCY_CHECK) {
        // In the previous version we were not able to detect this problem
        assert_success!(result)
    } else {
        assert_vm_status!(result, StatusCode::CONSTRAINT_NOT_SATISFIED)
    }
}
```
