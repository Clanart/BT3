### Title
Missing Validation of `compiled_class_hash` in Declare Transaction Allows Permanent Freezing of Contract Funds - (File: `execution/transaction_impls.cairo`)

---

### Summary

The `execute_declare_transaction` function in `transaction_impls.cairo` rigorously validates the Sierra class hash but performs no meaningful validation on the `compiled_class_hash` field supplied by the transaction sender. Any non-zero felt value is accepted. An unprivileged user can declare a class with a valid Sierra class hash paired with an arbitrary, non-existent compiled class hash. Any contract subsequently deployed under that class hash becomes permanently unexecutable, freezing all funds held within it.

---

### Finding Description

In `execute_declare_transaction`, the Sierra class hash is cryptographically verified against the component hashes of the provided Sierra class: [1](#0-0) 

```cairo
let expected_class_hash = finalize_class_hash(
    contract_class_component_hashes=contract_class_component_hashes
);
with_attr error_message("Invalid class hash pre-image.") {
    assert [class_hash_ptr] = expected_class_hash;
}
```

Immediately after, the `compiled_class_hash` — the hash of the CASM (compiled Sierra class) that the OS will use to actually execute contracts of this class — is stored with only a non-zero check: [2](#0-1) 

```cairo
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
```

There is no check that `compiled_class_hash` corresponds to any entry in the `compiled_class_facts_bundle` that the prover has provided. The post-execution validation in `os.cairo` only covers compiled class facts that were **actually used during execution** in the same block: [3](#0-2) 

```cairo
validate_compiled_class_facts_post_execution(
    n_compiled_class_facts=compiled_class_facts_bundle.n_compiled_class_facts,
    compiled_class_facts=compiled_class_facts_bundle.compiled_class_facts,
    builtin_costs=compiled_class_facts_bundle.builtin_costs,
);
```

A class that is declared but not executed in the same block is never subject to this validation. The invalid `compiled_class_hash` is committed to the global state without any cryptographic binding to real CASM.

The `compiled_class_hash` is included in the transaction hash computation: [4](#0-3) 

```cairo
poseidon_hash_update_single(item=class_hash);
poseidon_hash_update_single(item=compiled_class_hash);
```

This means the user's chosen (invalid) `compiled_class_hash` is signed into the transaction and faithfully replayed by the OS — but the OS never checks whether it is real.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once an invalid `compiled_class_hash` is written to the contract class state tree, the OS cannot generate a valid ZK proof for any execution of a contract deployed under that class hash. The prover would need to supply a compiled class whose Poseidon hash equals the stored (invalid) value; no such class exists. The `validate_compiled_class_facts_post_execution` check would reject any fabricated class. Therefore:

- Any contract deployed with the poisoned class hash is permanently unexecutable.
- Any ERC-20 tokens, ETH, or other assets held by such a contract are permanently frozen with no recovery path.
- The state corruption is committed to L1 via the valid ZK proof generated for the declare block, making it irreversible.

---

### Likelihood Explanation

Any account on StarkNet can submit a declare transaction. The only prerequisite is a valid Sierra class (to pass the Sierra class hash check at line 742). The attacker can craft a Sierra class with no constructor (so deployment succeeds without executing CASM), pair it with `compiled_class_hash = 1` (or any non-zero felt), and submit the declare transaction. The OS, as the authoritative on-chain validator in the ZK proof model, accepts it. The sequencer's off-chain mempool checks are not a security guarantee — the OS is the trust anchor. The attack requires no privileged access, no leaked keys, and no malicious operator.

---

### Recommendation

After loading `compiled_class_hash` from the transaction, validate that it is present in the `compiled_class_facts_bundle` provided by the prover. Specifically, before writing to `contract_class_changes`, assert that there exists a compiled class fact whose hash equals `compiled_class_hash`. This mirrors the existing Sierra class hash validation pattern and closes the asymmetry between the two hash fields in a declare transaction.

---

### Proof of Concept

1. Attacker constructs a minimal Sierra class with no constructor entry point (so deployment does not invoke CASM).
2. Attacker submits a v3 declare transaction with:
   - `class_hash` = valid hash of the Sierra class (passes line 742 check).
   - `compiled_class_hash` = `1` (non-zero, passes line 816 check; no real CASM has this hash).
3. The OS processes the declare transaction, writes `class_hash → 1` into `contract_class_changes`, and generates a valid proof. L1 accepts the state update.
4. Attacker (or victim) deploys a contract under `class_hash`. Because the class has no constructor, the constructor execution is a no-op and deployment succeeds.
5. Victim sends funds to the deployed contract.
6. Any subsequent call to the contract requires the prover to supply a compiled class with hash `1`. No such class exists. The prover cannot generate a valid proof for any execution of this contract.
7. The contract is permanently unexecutable. All funds are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L116-120)
```text
    validate_compiled_class_facts_post_execution(
        n_compiled_class_facts=compiled_class_facts_bundle.n_compiled_class_facts,
        compiled_class_facts=compiled_class_facts_bundle.compiled_class_facts,
        builtin_costs=compiled_class_facts_bundle.builtin_costs,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L285-288)
```text
        // Add the class hash to the hash state.
        poseidon_hash_update_single(item=class_hash);
        poseidon_hash_update_single(item=compiled_class_hash);
    }
```
