### Title
Wrong Endianness Assumption When Decoding `vout` from EVM Storage Proof Causes Bridge Circuit to Reject Valid Operator Payouts — (`File: circuits-lib/src/bridge_circuit/storage_proof.rs`)

### Summary

`verify_storage_proofs` decodes the withdrawal `vout` from the EVM storage slot using `u32::from_le_bytes`, but EVM stores `uint32` values in big-endian, right-aligned format. For any withdrawal whose `vout` is non-zero, the decoded value is corrupted by a factor of `2^(8*(3-k))` (where `k` is the byte position of the non-zero byte), causing the bridge circuit to panic and preventing the operator from ever producing a valid reimbursement proof.

---

### Finding Description

In `circuits-lib/src/bridge_circuit/storage_proof.rs`, `verify_storage_proofs` reads the withdrawal output index from the verified EVM storage proof:

```rust
let buf: [u8; 32] = vout_storage_proof.value.to_be_bytes();

// ENDIANNESS SHOULD BE CHECKED THIS FIELD IS 4 BYTES in the contract
let vout = u32::from_le_bytes(
    buf[28..32]
        .try_into()
        .expect("Vout value conversion failed"),
);
``` [1](#0-0) 

`vout_storage_proof.value` is an alloy `U256`. `.to_be_bytes()` produces the canonical big-endian 32-byte representation. For a Solidity `uint32 vout = N`, EVM stores the value right-aligned in the 32-byte slot, so `buf[28..32]` is `[0x00, 0x00, 0x00, N]` (big-endian). Applying `u32::from_le_bytes` to that slice interprets the bytes in the wrong order:

| Actual vout | `buf[28..32]` | `from_le_bytes` result | `from_be_bytes` result |
|---|---|---|---|
| 0 | `[00 00 00 00]` | 0 ✓ | 0 ✓ |
| 1 | `[00 00 00 01]` | **16 777 216** ✗ | 1 ✓ |
| 2 | `[00 00 00 02]` | **33 554 432** ✗ | 2 ✓ |

The developer comment `// ENDIANNESS SHOULD BE CHECKED THIS FIELD IS 4 BYTES in the contract` confirms the assumption was never validated.

The corrupted `vout` is then asserted against the actual input index of the payout transaction:

```rust
assert_eq!(
    vout,
    input.payout_spv.transaction.input[payout_input_index]
        .previous_output
        .vout,
    "Invalid withdrawal transaction output index"
);
``` [2](#0-1) 

For any `vout != 0`, the assertion panics inside the ZK circuit, making it impossible for the operator to generate a valid bridge proof.

---

### Impact Explanation

The bridge circuit is the operator's sole mechanism to prove a legitimate payout and claim reimbursement from the bridge vault. If the circuit panics, the operator:

1. Cannot produce a valid Groth16 proof to submit in the assert/disprove flow.
2. Cannot be reimbursed from the bridge vault for the BTC they already paid out.
3. Has their collateral permanently locked or slashed by a challenger who observes the missing proof.

This is a direct, permanent loss of operator collateral and reimbursement outputs for every withdrawal whose on-chain UTXO has `vout ≥ 1`. The bridge's liveness is also broken for that class of withdrawals. [3](#0-2) 

---

### Likelihood Explanation

Bitcoin UTXOs with `vout = 0` are common but not universal. Any deposit transaction that places the bridge output at index 1 or higher (e.g., because the user's wallet puts change first, or because the deposit is the second output of a batch transaction) will produce a withdrawal request with `vout ≥ 1`. The bug is deterministically triggered by the vout value alone — no attacker action is required; any honest user whose deposit UTXO has non-zero vout will silently break the operator's reimbursement path.

---

### Recommendation

Replace `from_le_bytes` with `from_be_bytes` to match EVM's big-endian storage layout:

```rust
let buf: [u8; 32] = vout_storage_proof.value.to_be_bytes();
let vout = u32::from_be_bytes(
    buf[28..32]
        .try_into()
        .expect("Vout value conversion failed"),
);
```

Additionally, add a unit test that verifies `vout = 1` round-trips correctly through the storage proof decoder.

---

### Proof of Concept

**Setup:** A user deposits BTC in a transaction where the bridge output is at `vout = 1` (e.g., the wallet places a change output at index 0). The L2 contract records `(txid, vout=1)` as the withdrawal outpoint.

**Trigger:** An operator pays out the withdrawal and is subsequently challenged. The operator runs the bridge circuit with the correct storage proof. Inside `verify_storage_proofs`:

```
vout_storage_proof.value = U256::from(1)
buf = [0x00; 28] ++ [0x00, 0x00, 0x00, 0x01]
vout = u32::from_le_bytes([0x00, 0x00, 0x00, 0x01]) = 0x01000000 = 16_777_216
```

**Assertion failure:**
```
assert_eq!(16_777_216, 1, "Invalid withdrawal transaction output index")
// → PANIC inside ZK circuit
```

**Outcome:** The operator cannot produce a valid proof. The challenger wins by default. The operator's collateral is slashed and the reimbursement output is lost, despite the operator having performed a fully legitimate payout. [4](#0-3) [5](#0-4)

### Citations

**File:** circuits-lib/src/bridge_circuit/storage_proof.rs (L119-133)
```rust
    let buf: [u8; 32] = vout_storage_proof.value.to_be_bytes();

    // ENDIANNESS SHOULD BE CHECKED THIS FIELD IS 4 BYTES in the contract
    let vout = u32::from_le_bytes(
        buf[28..32]
            .try_into()
            .expect("Vout value conversion failed"),
    );

    let wd_outpoint = WithdrawalOutpointTxid(utxo_storage_proof.value.to_be_bytes());

    let move_txid = MoveTxid(deposit_storage_proof.value.to_be_bytes());

    (wd_outpoint, vout, move_txid)
}
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L183-204)
```rust
    let (user_wd_outpoint, vout, move_txid) =
        verify_storage_proofs(&input.sp, light_client_circuit_output.l2_state_root);

    let user_wd_txid = bitcoin::Txid::from_byte_array(*user_wd_outpoint);

    let payout_input_index: usize = input.payout_input_index as usize;

    assert_eq!(
        user_wd_txid,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .txid,
        "Invalid withdrawal transaction ID"
    );

    assert_eq!(
        vout,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .vout,
        "Invalid withdrawal transaction output index"
    );
```
