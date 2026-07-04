### Title
Missing Bounds Check on `proof_facts_size` Enables Unbounded Iteration in OS Proof Generation — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `proof_facts_size` field loaded from the hint `TxProofFacts` in `execute_invoke_function_transaction` is passed directly to `check_proof_facts` without any upper-bound assertion. Every other attacker-influenced array in the same code path is explicitly capped at `SIERRA_ARRAY_LEN_BOUND - 1`. A malicious transaction sender can craft an invoke transaction with an arbitrarily large `proof_facts` array; if the sequencer includes that transaction in a block that also contains consumed L1→L2 messages, the OS proof generation will exhaust its step budget, the block will be unprovable, and the already-consumed L1 funds will be permanently frozen.

---

### Finding Description

In `execute_invoke_function_transaction`, three attacker-supplied array sizes are loaded from hints and then used in OS-level iteration:

```cairo
// transaction_impls.cairo  ~L276-L281
local account_deployment_data_size;
local account_deployment_data: felt*;
%{ TxAccountDeploymentData %}
local proof_facts_size;
local proof_facts: felt*;
%{ TxProofFacts %}
```

Immediately after, `check_proof_facts` is called with the raw, unvalidated `proof_facts_size`:

```cairo
// transaction_impls.cairo  ~L313-L318
check_proof_facts(
    proof_facts_size=proof_facts_size,
    proof_facts=proof_facts,
    current_block_number=block_context.block_info_for_execute.block_number,
    virtual_os_config_hash=block_context.os_global_context.virtual_os_config_hash,
);
```

Contrast this with the explicit bounds enforcement applied to every other variable-length field in the same file:

```cairo
// transaction_impls.cairo  ~L218
assert_nn_le(signature_len, SIERRA_ARRAY_LEN_BOUND - 1);

// transaction_impls.cairo  ~L485
assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);

// transaction_impls.cairo  ~L534
assert_nn_le(constructor_calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
```

`proof_facts_size`, `account_deployment_data_size`, and `paymaster_data_length` receive no equivalent guard. `check_proof_facts` (in `execution_constraints.cairo`) must iterate over every element of `proof_facts` to verify each commitment against the current block number and OS config hash. With an unbounded `proof_facts_size`, that loop can consume an arbitrary number of Cairo steps.

The same unbounded pattern applies to `account_deployment_data_size`, which is fed into `compute_invoke_transaction_hash` and iterated during Poseidon hashing.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

The StarkNet OS is a ZK proof program. If it exhausts its step budget (or any other resource limit) while processing a block, no valid STARK proof can be generated for that block. The block is then permanently stuck: it cannot be committed to L1.

L1→L2 messages (L1 handler transactions) are consumed on L1 at the moment the L1 contract processes them — the ETH or ERC-20 tokens are already transferred. If the block containing those consumed messages cannot be proven, the corresponding L2 state update is never written to L1, and the funds are irrecoverably locked.

An attacker needs only one invoke transaction with a sufficiently large `proof_facts` array co-located in the same block as any L1 handler transaction to trigger this outcome.

---

### Likelihood Explanation

The attack path is fully reachable by an unprivileged transaction sender:

1. The sender submits an invoke transaction (v3) with a `proof_facts` array of, say, 2^20 elements. The transaction hash is computed over this array, so the hash is valid.
2. The gateway and mempool accept the transaction (they validate the signature and nonce, not the internal array sizes).
3. The batcher includes the transaction in a block alongside a legitimate L1 handler transaction.
4. The blockifier executes the transaction successfully (contract execution does not iterate `proof_facts`; only the OS does).
5. The OS attempts to prove the block, calls `check_proof_facts` with `proof_facts_size = 2^20`, exhausts its step budget, and fails to produce a proof.
6. The block is permanently unprovable; the L1 handler funds are frozen.

The inconsistency is subtle: every other array is bounded, so reviewers and tooling are likely to assume `proof_facts_size` is also bounded.

---

### Recommendation

Add an explicit upper-bound assertion immediately after loading `proof_facts_size` and `account_deployment_data_size`, mirroring the pattern already used for `signature_len` and `calldata_size`:

```cairo
// In execute_invoke_function_transaction, after %{ TxProofFacts %}
assert_nn_le(proof_facts_size, SIERRA_ARRAY_LEN_BOUND - 1);

// After %{ TxAccountDeploymentData %}
assert_nn_le(account_deployment_data_size, SIERRA_ARRAY_LEN_BOUND - 1);
```

Similarly, add the same guard for `paymaster_data_length` in `get_account_tx_common_fields`. These checks should also be enforced at the gateway/mempool layer so that oversized transactions are rejected before they reach the batcher.

---

### Proof of Concept

1. Deploy any Cairo 1 account contract on a testnet.
2. Craft a v3 invoke transaction whose `proof_facts` field contains `N` arbitrary felts (e.g., `N = 500_000`). Compute the correct Poseidon transaction hash over this large array and sign it.
3. Submit the transaction; the gateway accepts it (signature and nonce are valid).
4. Separately, send an L1→L2 message from L1 (e.g., a token deposit) so that an L1 handler transaction is queued.
5. Observe that the batcher includes both transactions in the same block.
6. The OS invokes `check_proof_facts` with `proof_facts_size = 500_000`, iterates over all elements, exhausts the prover's step limit, and fails to generate a proof.
7. The block is never committed to L1; the deposited tokens are permanently frozen.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L215-218)
```text
    local signature_start: felt*;
    local signature_len: felt;
    %{ GenSignatureArg %}
    assert_nn_le(signature_len, SIERRA_ARRAY_LEN_BOUND - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L276-318)
```text
    local account_deployment_data_size;
    local account_deployment_data: felt*;
    %{ TxAccountDeploymentData %}
    local proof_facts_size;
    local proof_facts: felt*;
    %{ TxProofFacts %}

    let poseidon_ptr = builtin_ptrs.selectable.poseidon;
    with poseidon_ptr {
        let transaction_hash = compute_invoke_transaction_hash(
            common_fields=common_tx_fields,
            execution_context=tx_execution_context,
            account_deployment_data_size=account_deployment_data_size,
            account_deployment_data=account_deployment_data,
            proof_facts_size=proof_facts_size,
            proof_facts=proof_facts,
        );
    }
    update_poseidon_in_builtin_ptrs(poseidon_ptr=poseidon_ptr);

    %{ AssertTransactionHash %}

    // Write the transaction info and complete the ExecutionInfo struct.
    tempvar tx_info = tx_execution_info.tx_info;
    fill_account_tx_info(
        transaction_hash=transaction_hash,
        common_tx_fields=common_tx_fields,
        account_deployment_data_size=account_deployment_data_size,
        account_deployment_data=account_deployment_data,
        proof_facts_size=proof_facts_size,
        proof_facts=proof_facts,
        tx_info_dst=tx_info,
        deprecated_tx_info_dst=tx_execution_context.deprecated_tx_info,
    );

    check_and_increment_nonce(tx_info=tx_info);

    check_proof_facts(
        proof_facts_size=proof_facts_size,
        proof_facts=proof_facts,
        current_block_number=block_context.block_info_for_execute.block_number,
        virtual_os_config_hash=block_context.os_global_context.virtual_os_config_hash,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L483-485)
```text
        deprecated_tx_info=deprecated_tx_info_ptr,
    );
    assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
```
