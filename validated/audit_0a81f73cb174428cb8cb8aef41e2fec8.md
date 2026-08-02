## Title
`object_code_deployment::publish` derives its object seed from `account::sequence_number`, which is never incremented for orderless (nonce-based) transactions, allowing deterministic code-object address collisions — ([File: aptos-move/framework/aptos-framework/sources/object_code_deployment.move])

## Summary
The external report's root cause is a deterministic ID generator (`hash(block.number, user)`) that fails to produce unique IDs across repeated/parallel calls, letting two independently-created orders collide and enabling a double refund. The Aptos-native analog I traced is `object_code_deployment::publish`'s `object_seed` function, which derives the new code-object's address from the *publisher's account sequence number*. With the framework's own "orderless transactions" replay-protection mode (nonce-based, `ReplayProtector::Nonce`), the sender's sequence number is explicitly *not* used or incremented, so the seed input becomes constant across multiple `publish` calls from the same sender in the same "epoch" of unincremented sequence number — reintroducing exactly the "deterministic-ID-without-entropy" collision class from the source report, now in a code-publish path instead of an order-book path.

## Finding Description
`object_code_deployment::publish` computes the new object's address as: [1](#0-0) 

```
public entry fun publish(publisher: &signer, metadata_serialized: vector<u8>, code: vector<vector<u8>>) {
    let publisher_address = signer::address_of(publisher);
    let object_seed = object_seed(publisher_address);
    let constructor_ref = &object::create_named_object(publisher, object_seed);
    ...
}

inline fun object_seed(publisher: address): vector<u8> {
    let sequence_number = account::get_sequence_number(publisher) + 1;
    let seeds = vector[];
    seeds.append(bcs::to_bytes(&OBJECT_CODE_DEPLOYMENT_DOMAIN_SEPARATOR));
    seeds.append(bcs::to_bytes(&sequence_number));
    seeds
}
```

The seed is fed into `object::create_named_object`, which derives the object address deterministically as `sha3_256(creator_addr | seed | 0xFE)`: [2](#0-1) 

The correctness of this scheme relies entirely on the invariant that `account::get_sequence_number(publisher)` strictly increases by exactly 1 for every transaction the publisher sends — this was true historically because every Aptos transaction consumed and incremented the sender's sequence number.

However, the framework now supports orderless transactions, which use a nonce-based replay protector instead of a sequence number: [3](#0-2) [4](#0-3) 

For these transactions, replay protection is enforced solely via `nonce_validation::check_and_insert_nonce` against an `(address, nonce)` table — there is no mutation of `Account.sequence_number`: [5](#0-4) 

Consistent with this, client/test infrastructure explicitly sets `sequence_number` to `u64::MAX` (a sentinel, not incremented per txn) and skips `increment_sequence_number()` for orderless transactions: [6](#0-5) [7](#0-6) 

Because `object_seed` still reads `account::get_sequence_number(publisher)` (the on-chain `Account.sequence_number` resource field, not the sentinel), and this field is never advanced by orderless transactions, any account that only ever submits orderless transactions will observe the exact same `object_seed` value (`sequence_number + 1`, i.e. the same constant) for every call to `object_code_deployment::publish`. The first `publish` call succeeds and creates the object at `sha3_256(publisher | seed | 0xFE)`. Any subsequent `publish` call from the same publisher, still using an orderless transaction and still before that account ever sends a sequence-number-based transaction, computes the identical seed and therefore the identical target object address.

## Impact Explanation
`object::create_named_object` aborts if `ObjectCore` already exists at the derived address (`assert!(!exists<ObjectCore>(object), EOBJECT_EXISTS)`), so a literal second `publish` to the same address is turned into a hard abort rather than silent overwrite — this limits, but does not eliminate, the exploitability. The core code-safety invariant broken is nonetheless real: the documented guarantee "Publishing modules flow: 1. Create a new object with the address derived from the publisher address **and the object seed** (implicitly assumed unique per publish)" no longer holds under orderless transactions, because the seed generation is silently degraded to a constant. This is a violation of the "object code deployment ... flows must not leak upgrade or freeze authority to unprivileged callers" / address-uniqueness invariant class: an attacker can pre-compute the deterministic address a victim's *next* `object_code_deployment::publish` call will target (since the seed depends only on the publisher's current, un-incremented sequence number) and front-run object creation at that address by any other primitive that can create a named object at an attacker-chosen address (e.g. depositing a resource, creating an unrelated named object with the same seed via `object::create_named_object` directly from the same address is not possible since only the publisher can create objects seeded by their own address — but the predictability itself is the defect: it removes the "unique address each time they are published" guarantee the module's own doc comment promises, and can cause `publish` to unexpectedly abort (denial of service on deployment) or, if any other code path derives/reuses the same seed formula off-chain expecting freshness (e.g. `large_packages.move`'s `publish_to_object`, tooling that pre-computes object addresses for a "next deployment"), to target the wrong/stale object.

## Likelihood Explanation
Exploitability depends on: (1) an account that exclusively uses orderless transactions (increasingly encouraged, since orderless mode is a first-class replay-protection mechanism in this codebase), and (2) that account calling `object_code_deployment::publish` more than once before ever sending a sequence-number-based transaction. This is a plausible, not contrived, usage pattern for new/rotated accounts or bots that adopt orderless transactions by default. It requires no privileged access and is triggered purely by normal user behavior — the vulnerable condition is a broken assumption in framework code, not caller error.

## Recommendation
`object_seed` must not depend on a value that is not guaranteed to change per transaction. Options:
- Derive the seed from the orderless nonce/replay-protector value already validated in the transaction (a `transaction_context`-exposed nonce) when replay protection type is `Nonce`, falling back to sequence number for `SequenceNumber`-protected transactions.
- Alternatively, use `transaction_context::generate_auid_address()` (already used elsewhere in `object.move` for guaranteed-fresh addresses) or incorporate the transaction hash/AUID counter into the seed so uniqueness does not depend on account sequence-number semantics at all.

## Proof of Concept
1. Enable orderless transactions (`replay_protection_nonce` set, no incrementing sequence number) as supported by `check_for_replay_protection_orderless_txn` / `nonce_validation::check_and_insert_nonce`.
2. From account `A` (which has never sent a sequence-number transaction, so `Account.sequence_number == 0`), submit an orderless transaction calling `object_code_deployment::publish(A, meta1, code1)`. Object seed = `hash(DOMAIN | bcs(0+1))`; object created at `addr1 = sha3_256(A | seed | 0xFE)`.
3. From the same account `A`, submit a second, independent orderless transaction calling `object_code_deployment::publish(A, meta2, code2)`. Because `A`'s `Account.sequence_number` is still `0` (orderless transactions never increment it), `object_seed` computes the identical seed as step 2, producing the identical `addr1`.
4. The second `publish` call aborts with `EOBJECT_EXISTS` inside `create_object_internal`, confirming the seed collision (observable denial-of-service on the second deployment) — demonstrating the address is not "unique each time," contradicting the module's documented guarantee.

Note: I was not able to fully trace, within the available index, whether any additional code path (CLI/tooling, indexer, or off-chain address-prediction logic) relies on `object_seed`'s "next sequence number" formula to pre-compute deployment addresses for orderless senders; that would elevate this from a self-inflicted DoS to a spoofing/address-prediction issue and should be checked in a full repository session.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L80-104)
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

    inline fun object_seed(publisher: address): vector<u8> {
        let sequence_number = account::get_sequence_number(publisher) + 1;
        let seeds = vector[];
        seeds.append(bcs::to_bytes(&OBJECT_CODE_DEPLOYMENT_DOMAIN_SEPARATOR));
        seeds.append(bcs::to_bytes(&sequence_number));
        seeds
    }
```

**File:** aptos-move/framework/aptos-framework/sources/object.move (L218-264)
```text
    /// Derives an object address from source material: sha3_256([creator address | seed | 0xFE]).
    public fun create_object_address(source: &address, seed: vector<u8>): address {
        let bytes = bcs::to_bytes(source);
        bytes.append(seed);
        bytes.push_back(OBJECT_FROM_SEED_ADDRESS_SCHEME);
        from_bcs::to_address(hash::sha3_256(bytes))
    }

    native fun create_user_derived_object_address_impl(source: address, derive_from: address): address;

    /// Derives an object address from the source address and an object: sha3_256([source | object addr | 0xFC]).
    ///
    /// Only used for primary_fungible_store.
    public fun create_user_derived_object_address(source: address, derive_from: address): address {
        create_user_derived_object_address_impl(source, derive_from)
    }

    /// Derives an object from an Account GUID.
    public fun create_guid_object_address(source: address, creation_num: u64): address {
        let id = guid::create_id(source, creation_num);
        let bytes = bcs::to_bytes(&id);
        bytes.push_back(OBJECT_FROM_GUID_ADDRESS_SCHEME);
        from_bcs::to_address(hash::sha3_256(bytes))
    }

    native fun exists_at<T: key>(object: address): bool;

    /// Returns the address of within an ObjectId.
    public fun object_address<T: key>(self: &Object<T>): address {
        self.inner
    }

    /// Convert Object<X> to Object<Y>.
    public fun convert<X: key, Y: key>(self: Object<X>): Object<Y> {
        address_to_object<Y>(self.inner)
    }

    /// Create a new named object and return the ConstructorRef. Named objects can be queried globally
    /// by knowing the user generated seed used to create them. Named objects cannot be deleted.
    ///
    /// Note that object returned will be owned by creator, and so creator still can do thing directly to the object,
    /// for example withdraw from fungible stores signer would own.
    public fun create_named_object(creator: &signer, seed: vector<u8>): ConstructorRef {
        let creator_address = signer::address_of(creator);
        let obj_addr = create_object_address(&creator_address, seed);
        create_object_internal(creator_address, obj_addr, false)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/transaction_validation.move (L169-185)
```text
        // Check for replay protection
        match (replay_protector) {
            SequenceNumber(txn_sequence_number) => {
                check_for_replay_protection_regular_txn(
                    sender_address,
                    gas_payer_address,
                    txn_sequence_number,
                );
            },
            Nonce(nonce) => {
                check_for_replay_protection_orderless_txn(
                    sender_address,
                    nonce,
                    txn_expiration_time,
                );
            }
        };
```

**File:** aptos-move/framework/aptos-framework/sources/transaction_validation.move (L244-255)
```text
    fun check_for_replay_protection_orderless_txn(
        sender: address,
        nonce: u64,
        txn_expiration_time: u64,
    ) {
        // prologue_common already checks that the current_time > txn_expiration_time
        assert!(
            txn_expiration_time <= timestamp::now_seconds() + MAX_EXP_TIME_SECONDS_FOR_ORDERLESS_TXNS,
            error::invalid_argument(PROLOGUE_ETRANSACTION_EXPIRATION_TOO_FAR_IN_FUTURE),
        );
        assert!(nonce_validation::check_and_insert_nonce(sender, nonce, txn_expiration_time), error::invalid_argument(PROLOGUE_ENONCE_ALREADY_USED));
    }
```

**File:** aptos-move/framework/aptos-framework/sources/nonce_validation.move (L127-204)
```text
    // Returns true if the input (address, nonce) pair doesn't exist in the nonce history, and inserted into nonce history successfully.
    // Returns false if the input (address, nonce) pair already exists in the nonce history.
    public(friend) fun check_and_insert_nonce(
        sender_address: address,
        nonce: u64,
        txn_expiration_time: u64,
    ): bool acquires NonceHistory {
        assert!(exists<NonceHistory>(@aptos_framework), error::invalid_state(E_NONCE_HISTORY_DOES_NOT_EXIST));
        // Check if the transaction expiration time is too far in the future.
        assert!(txn_expiration_time <= timestamp::now_seconds() + NONCE_REPLAY_PROTECTION_OVERLAP_INTERVAL_SECONDS, error::invalid_argument(ETRANSACTION_EXPIRATION_TOO_FAR_IN_FUTURE));
        let nonce_history = &mut NonceHistory[@aptos_framework];
        let nonce_key = NonceKey {
            sender_address,
            nonce,
        };
        let bucket_index = sip_hash_from_value(&nonce_key) % NUM_BUCKETS;
        let current_time = timestamp::now_seconds();
        if (!nonce_history.nonce_table.contains(bucket_index)) {
            nonce_history.nonce_table.add(
                bucket_index,
                empty_bucket(false)
            );
        };
        let bucket = nonce_history.nonce_table.borrow_mut(bucket_index);

        let existing_exp_time = bucket.nonce_to_exp_time_map.get(&nonce_key);
        if (existing_exp_time.is_some()) {
            let existing_exp_time = existing_exp_time.extract();

            // If the existing (address, nonce) pair has not expired, return false.
            if (existing_exp_time >= current_time) {
                return false;
            };

            // We maintain an invariant that two transaction with the same (address, nonce) pair cannot be stored
            // in the nonce history if their transaction expiration times are less than `NONCE_REPLAY_PROTECTION_OVERLAP_INTERVAL_SECONDS`
            // seconds apart.
            if (txn_expiration_time <= existing_exp_time + NONCE_REPLAY_PROTECTION_OVERLAP_INTERVAL_SECONDS) {
                return false;
            };

            // If the existing (address, nonce) pair has expired, garbage collect it.
            bucket.nonce_to_exp_time_map.remove(&nonce_key);
            bucket.nonces_ordered_by_exp_time.remove(&NonceKeyWithExpTime {
                txn_expiration_time: existing_exp_time,
                sender_address,
                nonce,
            });
        };

        // Garbage collect upto MAX_ENTRIES_GARBAGE_COLLECTED_PER_CALL expired nonces in the bucket.
        let i = 0;
        while (i < MAX_ENTRIES_GARBAGE_COLLECTED_PER_CALL && !bucket.nonces_ordered_by_exp_time.is_empty()) {
            let (front_k, _) = bucket.nonces_ordered_by_exp_time.borrow_front();
            // We garbage collect a nonce after it has expired and the NONCE_REPLAY_PROTECTION_OVERLAP_INTERVAL_SECONDS
            // seconds have passed.
            if (front_k.txn_expiration_time + NONCE_REPLAY_PROTECTION_OVERLAP_INTERVAL_SECONDS < current_time) {
                bucket.nonces_ordered_by_exp_time.pop_front();
                bucket.nonce_to_exp_time_map.remove(&NonceKey {
                    sender_address: front_k.sender_address,
                    nonce: front_k.nonce,
                });
            } else {
                break;
            };
            i += 1;
        };

        // Insert the (address, nonce) pair in the bucket.
        let nonce_key_with_exp_time = NonceKeyWithExpTime {
            txn_expiration_time,
            sender_address,
            nonce,
        };
        bucket.nonces_ordered_by_exp_time.add(nonce_key_with_exp_time, true);
        bucket.nonce_to_exp_time_map.add(nonce_key, txn_expiration_time);
        true
    }
```

**File:** api/test-context/src/test_context.rs (L1157-1207)
```rust
        let mut request = if self.use_orderless_transactions {
            let mut rng = rand::thread_rng();
            let replay_protection_nonce: u64 = rng.r#gen();
            json!({
                "sender": account.address(),
                "sequence_number": (u64::MAX).to_string(),
                "gas_unit_price": "100",
                "max_gas_amount": "1000000",
                "expiration_timestamp_secs": self.get_expiration_time().to_string(),
                "payload": payload,
                "replay_protection_nonce": replay_protection_nonce.to_string(),
            })
        } else {
            json!({
                "sender": account.address(),
                "sequence_number": account.sequence_number().to_string(),
                "gas_unit_price": "100",
                "max_gas_amount": "1000000",
                "expiration_timestamp_secs": "16373698888888",
                "payload": payload,
            })
        };

        let resp = self
            .post(
                self.api_specific_config.signing_message_endpoint(),
                request.clone(),
            )
            .await;

        let signing_msg = self
            .api_specific_config
            .unwrap_signing_message_response(resp);

        let sig = account
            .private_key()
            .sign_arbitrary_message(signing_msg.inner());

        request["signature"] = json!({
            "type": "ed25519_signature",
            "public_key": HexEncodedBytes::from(account.public_key().to_bytes().to_vec()),
            "signature": HexEncodedBytes::from(sig.to_bytes().to_vec()),
        });

        self.expect_status_code(status_code)
            .post("/transactions", request)
            .await;
        self.commit_mempool_txns(1).await;
        if !self.use_orderless_transactions {
            account.increment_sequence_number();
        }
```

**File:** sdk/src/transaction_builder.rs (L163-177)
```rust
        let sequence_number = if self.has_nonce() {
            u64::MAX
        } else {
            self.sequence_number
                .expect("sequence number must have been set")
        };
        RawTransaction::new(
            self.sender.expect("sender must have been set"),
            sequence_number,
            self.payload,
            self.max_gas_amount,
            self.gas_unit_price,
            self.expiration_timestamp_secs,
            self.chain_id,
        )
```
