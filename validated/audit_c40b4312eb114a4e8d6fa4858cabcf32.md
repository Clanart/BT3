No vulnerability found for this question.

**Analysis:**

The scenario described does not hold up against the actual publish flow. `code::publish_package` reads the object's `root_owner()` synchronously, in the same call, immediately before calling `init::record_deploy_owner`: [1](#0-0) 

Critically, the only way to invoke `publish_package` against an *existing* object address (i.e., one that "previously hosted a module") is through `object_code_deployment::upgrade`, which requires the caller to already be the object's current owner *before* the code-object signer is even generated: [2](#0-1) 

The `code_signer` used to call `code::publish_package_txn` is derived from the `ExtendRef` stored in `ManagingRefs`, which can only be obtained after passing the `object::is_owner(code_object, publisher_address)` assertion. There is no time-of-check/time-of-use gap: ownership is checked, the signer is minted, and `publish_package` immediately re-reads `root_owner()` within the same transaction/call — so whoever is recorded as `owner` in `record_deploy_owner` is, by construction, the actual legitimate current owner performing the republish, not an attacker impersonating a prior owner.

This is confirmed as intended design (not a bug) by the module's own test suite: [3](#0-2) 

This test explicitly demonstrates that after a legitimate transfer, republishing under the new (rightful) owner correctly updates the recorded `deploy_owner`, and that this is the expected/desired behavior — republishing re-arms self-init for the new owner. Conversely, sibling modules that are *not* republished remain locked to the original owner: [4](#0-3) 

There is no unprivileged path by which an entity that is *not* the current `root_owner` of the object can trigger `publish_package`/`record_deploy_owner` on that address — the ownership check in `object_code_deployment::upgrade` (and the fact that fresh `object_code_deployment::publish` always targets a brand-new, sequence-number-derived address) closes off the described attack. The premise that "attacker's later claim" could diverge from "actual root_owner at publish time" is not realizable: they are the same value read at the same point in the same transaction, and reaching that code path already requires proof of current ownership.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L113-133)
```text
    public entry fun upgrade(
        publisher: &signer,
        metadata_serialized: vector<u8>,
        code: vector<vector<u8>>,
        code_object: Object<PackageRegistry>,
    ) {
        let publisher_address = signer::address_of(publisher);
        assert!(
            object::is_owner(code_object, publisher_address),
            error::permission_denied(ENOT_CODE_OBJECT_OWNER),
        );

        let code_object_address = code_object.object_address();
        assert!(exists<ManagingRefs>(code_object_address), error::not_found(ECODE_OBJECT_DOES_NOT_EXIST));

        let extend_ref = &borrow_global<ManagingRefs>(code_object_address).extend_ref;
        let code_signer = &extend_ref.generate_signer_for_extending();
        code::publish_package_txn(code_signer, metadata_serialized, code);

        event::emit(Upgrade { object_address: signer::address_of(code_signer), });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/init.move (L244-259)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/init.move (L261-277)
```text
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
