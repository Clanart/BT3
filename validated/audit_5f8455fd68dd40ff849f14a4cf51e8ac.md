## Finding

### Title
Unbounded ownership-chain traversal in `Object::root_owner()` allows permanent denial-of-service of object-hosted package upgrades and lazy module self-initialization - (File: `aptos-move/framework/aptos-framework/sources/object.move`)

### Summary
`object::root_owner()` walks up an object's ownership chain with no cycle/depth protection, unlike every other ownership-traversal helper in the same module. This function is the single source of truth that `code::publish_package` and `init::assert_may_self_initialize` rely on to record and validate the "deploy owner" used to gate lazy module self-initialization and object-code republishing. Because Aptos objects can be made to own one another in a cycle via the ordinary `object::transfer_to_object` entry function, calling `root_owner()` on an object whose ownership chain contains a cycle loops forever (until gas exhaustion), permanently bricking any transaction that needs to publish/upgrade code at that object address or self-initialize a module hosted there.

### Finding Description
`root_owner()` is defined as: [1](#0-0) 

Compare this to the two other ownership-chain walkers in the same file, `verify_ungated_and_descendant` and `owns`, both of which explicitly bound their loops with `MAXIMUM_OBJECT_NESTING` to guard against cycles/DoS: [2](#0-1) [3](#0-2) 

`root_owner()` has no such bound. It is used by the custom "lazy module initialization" feature to record and later validate which address is authorized to self-initialize/republish an object-hosted module:
- On every publish/upgrade, `code::publish_package` records the *current* root owner for each module in the package being published, gated on the object address: [4](#0-3) 
- On self-init, `init::assert_may_self_initialize` re-derives the *current* root owner and compares it to the recorded one: [5](#0-4) 

Aptos objects can legitimately own each other, and nothing in `object::transfer_to_object` / `transfer_raw` prevents forming an ownership cycle among objects the caller currently controls: [6](#0-5) 

An account that owns two objects `P` and `Q` can transfer `P` to `Q` and `Q` to `P`, each transfer independently passing `verify_ungated_and_descendant` (which only checks that the signer is upstream of the object *being moved*, not that the destination is downstream-free), producing a 2-cycle `P -> Q -> P`. If any object-hosted module `M` is (or later becomes) owned, directly or transitively, by `P` or `Q`, then every call to `root_owner()` on `M` traverses `M -> ... -> P -> Q -> P -> Q -> ...` forever.

Any subsequent call to `code::publish_package` for `M` (i.e. `object_code_deployment::upgrade`) or any invocation reaching `init::internal_maybe_initialize` for a not-yet-initialized module in `M` will hit the unbounded loop and abort only via gas exhaustion, at unpredictable cost and with no recovery path — this is a corrupted, uncomputable "deploy owner" value that can never be resolved once the ownership graph above the object contains a cycle.

### Impact Explanation
This directly breaks two protected state-mutation paths gated by publish/ownership invariants:
1. **Permanent inability to upgrade or republish object-hosted code** — `code::publish_package` cannot complete for the affected object, since it unconditionally calls `root_owner()` when the lazy-init feature is enabled and the destination is an object.
2. **Permanent denial of module self-initialization** — any entry point relying on `init::internal_maybe_initialize` for that module can never successfully mint its self-init signer, since `assert_may_self_initialize` also calls `root_owner()` and will abort every time.

Both effects are irreversible: there is no way to "unfreeze" or clean up an ownership cycle once formed, and no cycle-detection exists anywhere in the write path. This effectively achieves an involuntary, un-revocable freeze/DoS of code-object upgrade and initialization capability — a code-safety invariant violation squarely in the "unauthorized ... freeze[...] or code replacement" and "module-init ... failure that reaches protected state mutation" categories. Because the object graph and its cyclic potential is fully attacker/owner-controlled and the resulting condition is permanent and unrecoverable, the impact is High.

### Likelihood Explanation
Likelihood is Low-to-Medium: creating the cycle requires the attacker (or whoever controls the object hierarchy above the target module) to explicitly execute two `transfer_to_object` calls forming a 2-cycle, and to place the target module's hosting object under that cyclic ancestry (possible since object owners can always transfer objects they control, including code-object addresses managed via `ManagingRefs`/`ExtendRef`). This is not accidental — it requires deliberate action — but it requires no special privilege beyond normal object ownership, which is the standard, permissionless capability every object owner has.

### Recommendation
Bound `root_owner()`'s traversal exactly as `verify_ungated_and_descendant`/`owns` do, using `MAXIMUM_OBJECT_NESTING`, and either abort or fall back to a defined "no root owner" sentinel when the bound is exceeded, so `code::publish_package` and `init::assert_may_self_initialize` fail predictably (and cheaply) instead of looping unboundedly:

```diff
 public fun root_owner<T: key>(self: Object<T>): address {
     let obj_owner = self.owner();
-    while (is_object(obj_owner)) {
+    let count = 0;
+    while (is_object(obj_owner)) {
+        count += 1;
+        assert!(count < MAXIMUM_OBJECT_NESTING, error::out_of_range(EMAXIMUM_NESTING));
         obj_owner = address_to_object<ObjectCore>(obj_owner).owner();
     };
     obj_owner
 }
```

### Proof of Concept
```move
// 1. Attacker owns objects P and Q, and separately deploys module M as an object
//    (e.g. via object_code_deployment::publish), with lazy module init enabled.
let p_ref = object::create_object(attacker_addr);
let q_ref = object::create_object(attacker_addr);
let p = object::object_from_constructor_ref<ObjectCore>(&p_ref);
let q = object::object_from_constructor_ref<ObjectCore>(&q_ref);

// 2. Form a 2-cycle: P -> Q -> P.
object::transfer_to_object(&attacker_signer, p, q); // P.owner = Q
object::transfer_to_object(&attacker_signer, q, p); // Q.owner = P  (now P<->Q cycle)

// 3. Move M under the cycle.
object::transfer_call(&attacker_signer, m_addr, object::object_address(&p));

// 4. Any subsequent call to code::publish_package for M (e.g. object_code_deployment::upgrade),
//    or any entry function on M relying on init::internal_maybe_initialize for a
//    not-yet-initialized module, now calls object::root_owner() on M, which walks
//    M -> P -> Q -> P -> Q -> ... forever, aborting only via out-of-gas.
//    M's code can never be upgraded or self-initialized again.
```

Note: I could not fully verify whether any additional framework-level guard (outside `object.move`, `code.move`, and `init.move`) prevents object ownership cycles from forming elsewhere in the codebase; my search covered the object/code/init publish path but not every possible caller of `transfer_to_object`. If such a guard exists, it should be checked before treating this as fully exploitable end-to-end.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/object.move (L572-603)
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

    /// Transfer the given object to another object. See `transfer` for more information.
    public entry fun transfer_to_object<O: key, T: key>(
        owner: &signer,
        object: Object<O>,
        to: Object<T>,
    ) {
        transfer(owner, object, to.inner)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/object.move (L608-639)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/object.move (L710-737)
```text
    public fun owns<T: key>(object: Object<T>, owner: address): bool {
        let current_address = object.object_address();

        assert!(
            exists<ObjectCore>(current_address),
            error::not_found(EOBJECT_DOES_NOT_EXIST),
        );

        if (current_address == owner) {
            return true
        };

        let object = borrow_global<ObjectCore>(current_address);
        let current_address = object.owner;

        let count = 0;
        while (owner != current_address) {
            count += 1;
            assert!(count < MAXIMUM_OBJECT_NESTING, error::out_of_range(EMAXIMUM_NESTING));
            if (!exists<ObjectCore>(current_address)) {
                return false
            };

            let object = borrow_global<ObjectCore>(current_address);
            current_address = object.owner;
        };
        true
    }
```

**File:** aptos-move/framework/aptos-framework/sources/object.move (L742-748)
```text
    public fun root_owner<T: key>(self: Object<T>): address {
        let obj_owner = self.owner();
        while (is_object(obj_owner)) {
            obj_owner = address_to_object<ObjectCore>(obj_owner).owner();
        };
        obj_owner
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
