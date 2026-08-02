No vulnerability found for this question.

**Reasoning:**

The wildcard (`module_name == ""`) mechanism in `allowed_deps` is not attacker-controllable in the way the question implies.

1. **Wildcard entries are only auto-generated for core framework addresses.** In `check_dependencies`, the `AllowedDep { account, module_name: "" }` wildcard is pushed only when `is_policy_exempted_address(dep.account)` is true, which restricts it to addresses `@1` through `@10` (the core Aptos framework addresses): [1](#0-0) [2](#0-1) 

2. **No public constructor exists for `AllowedDep`.** The struct only has `drop` (no `copy`/`store`), and it is populated exclusively inside the private `check_dependencies` function — there is no public/entry function that lets a caller pass in an arbitrary `AllowedDep { account, module_name: "" }` for an address they don't already control: [3](#0-2) 

3. **For non-exempted (i.e., any attacker-reachable) addresses**, the allowed module names populated into `allowed_module_deps` come directly from iterating the real, already-published `dep_pack.modules` list of the dependency package — not from user-supplied strings, and never as `""`: [4](#0-3) 

4. **On the VM side**, `validate_publish_request` treats `""` as a wildcard match for any dependency name under that account, but the `allowed_deps` map it receives is constructed exclusively via the `code.move` path above (or synthesized from the publisher's own `destination`/`expected_modules` in the native, which only adds the publisher's own modules, not a wildcard): [5](#0-4) [6](#0-5) 

Since the wildcard can only ever be attached to the ten reserved/exempted framework addresses — which are not attacker-owned or attacker-writable through this flow — an unprivileged publisher cannot fabricate a wildcard `allowed_deps` entry to bypass dependency-name restrictions for an arbitrary address. This does not meet the Publish Impact Gate criteria (no unauthorized module publish, upgrade, freeze, or dependency-bypass path is reachable from unprivileged input).

### Citations

**File:** aptos-move/framework/aptos-framework/sources/code.move (L306-311)
```text
            assert!(exists<PackageRegistry>(dep.account), error::not_found(EPACKAGE_DEP_MISSING));
            if (is_policy_exempted_address(dep.account)) {
                // Allow all modules from this address, by using "" as a wildcard in the AllowedDep
                let account: address = dep.account;
                let module_name = string::utf8(b"");
                vector::push_back(&mut allowed_module_deps, AllowedDep { account, module_name });
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L328-336)
```text
                        // Add allowed deps
                        let account = dep.account;
                        let k = 0;
                        let r = vector::length(&dep_pack.modules);
                        while (k < r) {
                            let module_name = vector::borrow(&dep_pack.modules, k).name;
                            vector::push_back(&mut allowed_module_deps, AllowedDep { account, module_name });
                            k += 1;
                        };
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L351-354)
```text
    fun is_policy_exempted_address(addr: address): bool {
        addr == @1 || addr == @2 || addr == @3 || addr == @4 || addr == @5 ||
            addr == @6 || addr == @7 || addr == @8 || addr == @9 || addr == @10
    }
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L374-382)
```text
    /// A helper type for request_publish_with_allowed_deps
    struct AllowedDep has drop {
        /// Address of the module.
        account: address,
        /// Name of the module. If this is the empty string, then this serves as a wildcard for
        /// all modules from this address. This is used for speeding up dependency checking for packages from
        /// well-known framework addresses, where we can assume that there are no malicious packages.
        module_name: String
    }
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1823-1837)
```rust
            if let Some(allowed) = &allowed_deps {
                for dep in m.immediate_dependencies() {
                    if !allowed
                        .get(dep.address())
                        .map(|modules| {
                            modules.contains("") || modules.contains(dep.name().as_str())
                        })
                        .unwrap_or(false)
                    {
                        return Err(Self::metadata_validation_error(&format!(
                            "unregistered dependency: '{}'",
                            dep
                        )));
                    }
                }
```

**File:** aptos-move/framework/natives/src/code.rs (L337-344)
```rust
    // Add own modules to allowed deps
    let allowed_deps = allowed_deps.map(|mut allowed| {
        allowed
            .entry(destination)
            .or_default()
            .extend(expected_modules.clone());
        allowed
    });
```
