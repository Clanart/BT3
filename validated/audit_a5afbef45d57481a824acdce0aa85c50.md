## Title
Unbounded ownership-chain traversal in `object::root_owner` lets an attacker permanently brick a code object's self-init and upgrade path via an ownership cycle - (File: `aptos-move/framework/aptos-framework/sources/object.move`)

### Summary
The lazy module self-initialization feature (`aptos_framework::init`) and the code-publishing path (`aptos_framework::code::publish_package`) both call `object::root_owner()` to resolve the transitive owner of an object-hosted package, in order to gate a security check (`assert_may_self_initialize`) and to record the owning address at publish time (`init::record_deploy_owner`). `root_owner()` walks up the object ownership chain with **no depth bound and no cycle detection**, unlike the sibling function `verify_ungated_and_descendant`, which explicitly limits traversal to `MAXIMUM_OBJECT_NESTING` (8) hops. Because `object::transfer_raw_inner` never validates the destination address when changing an object's owner, any account can construct a two-(or more-)object ownership cycle among objects it owns, including a previously-published code object. Once such a cycle exists, every call to `root_owner()` on that object loops forever (until gas runs out), permanently breaking both the object's self-init path and any future `publish_package`/upgrade/freeze call that targets it.

### Finding Description
`root_owner` is defined without any loop bound: [1](#0-0) 

Compare this to `verify_ungated_and_descendant`, which is used for ordinary ownership-chain checks and explicitly bounds the walk to `MAXIMUM_OBJECT_NESTING`: [2](#0-1) 

Ownership transfers, however, place no restriction on the destination address when actually mutating `ObjectCore.owner`: [3](#0-2) 

`transfer_raw` (and the public entry points `transfer`/`transfer_call` that call it) only verify that the *caller* currently owns the object being transferred (via the bounded `verify_ungated_and_descendant`); it never validates that `to` is not itself part of the ownership chain being modified. This lets an owner construct a genuine two-object ownership cycle: create objects `A` and `B` (both owned by the caller), transfer `A` to `B` (valid, caller owns `A`), then transfer `B` to `A` (valid, caller owns `B`) — leaving `A.owner == B` and `B.owner == A`.

This directly matters for code publishing: when the lazy-module-initialization feature is active, `code::publish_package` calls `object::address_to_object::<ObjectCore>(addr).root_owner()` for every object-hosted package publish/upgrade, to record the deploy owner: [4](#0-3) 

and `init::assert_may_self_initialize` calls `root_owner()` again on every self-init attempt to check that ownership hasn't changed since deploy: [5](#0-4) 

If the code object (or an ancestor in its ownership chain) is placed inside a cycle after being published, both of these `root_owner()` calls loop indefinitely, consuming gas until the transaction aborts on an out-of-gas error — with no way to make forward progress, since the aborted transaction leaves the cyclic ownership state untouched.

### Impact Explanation
This breaks a code-safety invariant of the publish path: an unprivileged object owner can render a previously-legitimate code object **permanently unmanageable** —
- Any future `object_code_deployment::upgrade`/`code::publish_package` call targeting that object address will hang/burn gas and abort, because `record_deploy_owner`'s `root_owner()` call never terminates normally. This blocks the intended upgrade authority (`freeze_code_object` and `upgrade`) from ever succeeding again.
- Every ordinary user transaction calling any entry function that goes through `init::internal_maybe_initialize` on that module will similarly loop/burn gas and abort, denying service to all callers of the module, not just the attacker.

This is a direct DoS/griefing analog of the reported `address(0)` bug: a value fully controlled by an unprivileged actor (the ownership graph) is accepted without a bound, and later poisons a privileged operation (publish/upgrade/freeze and self-init) that legitimate parties cannot work around, matching the "Object code deployment ... must not leak upgrade or freeze authority to unprivileged callers" / code-safety pivot.

### Likelihood Explanation
No special privilege is required: an attacker only needs to own the objects involved in the cycle, which is trivially true for any object they create or any code object they deployed themselves via `object_code_deployment::publish`. Constructing the cycle takes only two standard `object::transfer`/`transfer_call` entry-function calls. The only precondition is that `FeatureFlag::LAZY_MODULE_INITIALIZATION` (feature 127) be enabled network-wide, at which point the bug is reachable by any account with no admin/governance cooperation needed.

### Recommendation
Bound `root_owner()`'s traversal the same way `verify_ungated_and_descendant` is bounded (e.g., cap at `MAXIMUM_OBJECT_NESTING` hops and abort with a clear error if the chain does not terminate), and/or reject ownership transfers that would introduce a cycle by checking, before mutating `ObjectCore.owner`, that `to` is not already reachable from the object being transferred (walking the existing chain with the same bound).

### Proof of Concept
1. Attacker calls `object_code_deployment::publish` to deploy a package to a fresh object `addr` (owner = attacker), with `LAZY_MODULE_INITIALIZATION` enabled; `code::publish_package` records `deploy_owner = attacker` for `addr`'s modules via `init::record_deploy_owner`.
2. Attacker creates a second object `B` (owner = attacker) via `object::create_object`/`create_named_object`.
3. Attacker calls `object::transfer_call(attacker, addr, B)` — valid, since attacker owns `addr`; now `addr.owner == B`.
4. Attacker calls `object::transfer_call(attacker, B, addr)` — valid, since attacker owns `B`; now `B.owner == addr`. `addr` and `B` now form an ownership cycle.
5. Any subsequent call to an entry function on `addr`'s module that invokes `init::internal_maybe_initialize` (see test harness pattern in `aptos-move/e2e-move-tests/src/tests/init_module_api.rs` lines 242-344) triggers `assert_may_self_initialize` → `root_owner(addr)`, which loops between `addr` and `B` forever, exhausting gas and aborting the transaction for any caller.
6. Any attempt by the attacker (or a delegate holding the `ExtendRef`) to call `object_code_deployment::upgrade`/`freeze_code_object` on `addr` likewise triggers `code::publish_package`'s `root_owner()` call and fails the same way, permanently blocking upgrade/freeze. [6](#0-5)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/object.move (L572-594)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L181-187)
```text
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

**File:** aptos-move/e2e-move-tests/src/tests/init_module_api.rs (L298-344)
```rust
#[test]
fn init_maybe_initialize_object_owner_unchanged_runs() {
    let mut h = new_harness();
    let acc = h.new_account_at(AccountAddress::from_hex_literal(ADDR).unwrap());
    let obj = deploy_object_addr(&h, &acc);

    assert_success!(deploy_to_object(&mut h, &acc, obj));

    // Owner unchanged since deploy -> self-init is allowed.
    assert_success!(run_object(&mut h, &acc, obj));

    let value = h
        .read_resource::<Counter>(
            &obj,
            parse_struct_tag(&format!("{}::test::Counter", obj)).unwrap(),
        )
        .unwrap()
        .value;
    assert_eq!(value, 1);
}

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
