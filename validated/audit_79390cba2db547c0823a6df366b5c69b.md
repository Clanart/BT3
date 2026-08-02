Based on the code I was able to inspect, this is a real gap, though it stems from a documented design tradeoff rather than a hidden implementation bug. Here is the analysis.

### Title
`freeze_code_object` does not re-validate `PackageDep` upgrade-policy invariants, allowing a nominally "immutable" package to retain a weaker-policy dependency - (File: `aptos-move/framework/aptos-framework/sources/code.move`)

### Summary
`check_dependencies` is the only place that enforces `EDEP_WEAKER_POLICY` (a package's dependencies must have an upgrade policy `>=` its own), and it only runs inside `publish_package` at publish/upgrade time.<cite repo="bsaldua/aptos-core--007" path="aptos-move/framework/aptos-framework/sources/code.move" start="297="317="320" /> `freeze_code_object` never calls `check_dependencies`; it only checks object existence and ownership before flipping the policy field to immutable. [1](#0-0) 

### Finding Description
A package `P` can be published with `upgrade_policy = compat` and a `PackageDep` on package `D`, which also has `upgrade_policy = compat`. `check_dependencies` accepts this because `dep_pack.upgrade_policy.policy (1) >= pack.upgrade_policy.policy (1)`. [2](#0-1) 

`P`'s unprivileged owner then calls `object_code_deployment::freeze_code_object` (which forwards to `code::freeze_code_object`), which only verifies object existence and object ownership before marking `P` immutable - it does not re-run `check_dependencies` to confirm `D`'s policy is still `>= immutable`. [3](#0-2) [4](#0-3) 

`D`'s owner (an entirely separate, unprivileged account) can still call `upgrade`/`publish_package_txn` on `D` at any later time, subject only to `check_upgradability`'s compatibility rules (same public function signatures, no resource layout break) - not to any requirement to preserve behavior, nor is `D`'s owner ever informed that some now-immutable package depends on it. [5](#0-4) 

The result: `P` is displayed on-chain/to users as `upgrade_policy = immutable`, but its actual runtime behavior can still change because the Move loader resolves calls into `D` by `ModuleId` from whatever code currently lives at `D`'s address - dependency resolution is not pinned to the bytecode that existed at freeze time.

### Impact Explanation
This breaks the invariant, implied by the `immutable` policy name/documentation ("modules ... are immutable and cannot be upgraded"), that a frozen package's overall behavior is fixed forever. [6](#0-5)  Users, auditors, or other contracts that rely on "immutable" as a strong on-chain guarantee about `P` can be misled: `D`'s (unprivileged, unrelated) owner can alter behavior reachable through `P`'s calls without `P`'s owner's consent and without violating `D`'s own compat policy (compat only guarantees function signatures/layouts, not semantics).

However, note the mitigating factor: `check_dependencies`'s `EDEP_WEAKER_POLICY` check does apply at `P`'s *original* publish/upgrade time, so this only manifests when `P` was published as non-immutable (arbitrary/compat) with a same-or-weaker-policy dependency and *later* frozen without dependencies also being tightened - it does not let an attacker inject an already-immutable package with a weaker dependency in one step.

### Likelihood Explanation
Moderate-to-low. It requires a specific publish sequence (publish with compat/arbitrary dependency, then freeze) which is a plausible but non-default workflow for `object_code_deployment`. The `#[test] object_code_deployment_freeze_code_object` test in the repo only checks the policy field flips to immutable and does not test the dependency-consistency invariant at all. [7](#0-6) 

### Recommendation
Have `freeze_code_object` re-invoke (or reuse) the dependency-policy check from `check_dependencies` against the package's already-recorded `deps`, aborting with `EDEP_WEAKER_POLICY` if any dependency's current on-chain policy is now weaker than `immutable`. Alternatively, document explicitly (and enforce via the Move prover spec) that "immutable" only guarantees the package's own bytecode is frozen, not its transitive dependency graph, so integrators do not over-trust the guarantee. The current `code.spec.move` spec for `freeze_code_object` also does not model any dependency check, which should be updated to match whichever behavior is chosen. [8](#0-7) 

### Proof of Concept
1. Account `A` publishes package `D` with `upgrade_policy = compat` exposing function `f`.
2. Account `B` publishes package `P` (`upgrade_policy = compat`) with a `PackageDep` on `D`; `check_dependencies` passes since `compat >= compat`.
3. `B` calls `object_code_deployment::freeze_code_object` on `P`'s code object; `P.upgrade_policy` becomes `immutable`. No dependency re-check occurs. [4](#0-3) 
4. `A` (unrelated, unprivileged) publishes an upgrade to `D` that keeps `f`'s signature/layout intact (satisfying `check_upgradability`) but changes `f`'s internal logic/return semantics.
5. Any call from `P` into `D::f` now executes the new logic, even though `P.upgrade_policy` reads as `immutable` - demonstrating that `check_dependencies`'s policy invariant is enforced only at freeze-adjacent publish time, not re-verified by `freeze_code_object` itself.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/code.move (L134-137)
```text
    /// Whether the modules in the package are immutable and cannot be upgraded.
    public fun upgrade_policy_immutable(): UpgradePolicy {
        UpgradePolicy { policy: 2 }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L233-240)
```text
    public fun freeze_code_object(publisher: &signer, code_object: Object<PackageRegistry>) acquires PackageRegistry {
        let code_object_addr = code_object.object_address();
        assert!(exists<PackageRegistry>(code_object_addr), error::not_found(ECODE_OBJECT_DOES_NOT_EXIST));
        assert!(
            object::is_owner(code_object, signer::address_of(publisher)),
            error::permission_denied(ENOT_PACKAGE_OWNER)
        );

```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L271-280)
```text
        assert!(can_change_upgrade_policy_to(old_pack.upgrade_policy, new_pack.upgrade_policy),
            error::invalid_argument(EUPGRADE_WEAKER_POLICY));
        let old_modules = get_module_names(old_pack);

        old_modules.for_each_ref(|old_module| {
            assert!(
                vector::contains(new_modules, old_module),
                EMODULE_MISSING
            );
        });
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L313-321)
```text
                let registry = borrow_global<PackageRegistry>(dep.account);
                let found = vector::any(&registry.packages, |dep_pack| {
                    let dep_pack: &PackageMetadata = dep_pack;
                    if (dep_pack.name == dep.package_name) {
                        // Check policy
                        assert!(
                            dep_pack.upgrade_policy.policy >= pack.upgrade_policy.policy,
                            error::invalid_argument(EDEP_WEAKER_POLICY)
                        );
```

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L138-142)
```text
    public entry fun freeze_code_object(publisher: &signer, code_object: Object<PackageRegistry>) {
        code::freeze_code_object(publisher, code_object);

        event::emit(Freeze { object_address: code_object.object_address(), });
    }
```

**File:** aptos-move/e2e-move-tests/src/tests/object_code_deployment.rs (L322-345)
```rust
/// Tests the `freeze_code_object` object code deployment function.
#[test]
fn object_code_deployment_freeze_code_object() {
    let mut context = TestContext::new(None, None);
    let acc = context.account.clone();

    // First deploy the package to an object.
    assert_success!(context.execute_object_code_action(
        &acc,
        "object_code_deployment.data/pack_initial",
        ObjectCodeAction::Deploy,
    ));

    // Mark packages immutable.
    assert_success!(context.execute_object_code_action(&acc, "", ObjectCodeAction::Freeze));

    let registry = context
        .read_resource::<PackageRegistry>(&context.object_address, PACKAGE_REGISTRY_ACCESS_PATH)
        .unwrap();
    // Verify packages are immutable.
    for package in &registry.packages {
        assert_eq!(package.upgrade_policy, UpgradePolicy::immutable());
    }
}
```

**File:** aptos-move/framework/aptos-framework/sources/code.spec.move (L107-116)
```text
    spec freeze_code_object(publisher: &signer, code_object: Object<PackageRegistry>) {
        pragma aborts_if_is_partial;

        let code_object_addr = code_object.inner;
        aborts_if !exists<object::ObjectCore>(code_object_addr);
        aborts_if !exists<PackageRegistry>(code_object_addr);
        aborts_if !object::is_owner(code_object, signer::address_of(publisher));

        modifies global<PackageRegistry>(code_object_addr);
    }
```
