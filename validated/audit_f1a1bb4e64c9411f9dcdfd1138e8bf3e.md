### Title
Missing sighash-type enforcement in `Aggregator::optimistic_payout` lets attacker submit a withdrawal signature that never commits to `output_amount`/`output_script_pubkey` - ([File: core/src/rpc/aggregator.rs])

### Summary
`Aggregator::optimistic_payout` reads `input_signature.sighash_type` directly from the attacker-supplied `OptimisticWithdrawParams` and passes it unchecked into `calculate_pubkey_spend_sighash`, then verifies the signature against whatever output the aggregator just built from the attacker-supplied `output_amount`/`output_script_pubkey`. `calculate_pubkey_spend_sighash`'s `Prevouts::One` branch treats `SinglePlusAnyoneCanPay`, `AllPlusAnyoneCanPay`, and `NonePlusAnyoneCanPay` identically for *prevout* selection, but per BIP-341 these three types differ in whether the *destination output* is committed at all: `SIGHASH_NONE` omits the output digest entirely. This means a signature produced under `NonePlusAnyoneCanPay` does not bind `output_amount`/`output_script_pubkey`, so the same signature verifies for any amount/script the aggregator is asked to plug in.

### Finding Description
The intended binding is: `input_signature` commits to `output_amount == TxOut{0}.value` and `output_script_pubkey == TxOut{0}.script_pubkey` of the constructed optimistic-payout transaction, via the Taproot key-spend sighash.

Code path:
- `core/src/rpc/aggregator.rs:1024-1098` parses `deposit_id, input_signature, input_outpoint, output_script_pubkey, output_amount` straight from the request and builds `output_txout` from the attacker-supplied `output_amount`/`output_script_pubkey` with no cross-check against any Citrea-side committed withdrawal amount/script for that `deposit_id`. [1](#0-0) 
- `core/src/rpc/aggregator.rs:1112-1126` builds the tx handler with that `output_txout` and calls `calculate_pubkey_spend_sighash(0, input_signature.sighash_type)`, then `SECP.verify_schnorr` against the resulting message, with `input_signature.sighash_type` fully attacker-controlled. [2](#0-1) 
- `calculate_pubkey_spend_sighash` (`core/src/builder/transaction/txhandler.rs:222-229`) routes `SinglePlusAnyoneCanPay`, `AllPlusAnyoneCanPay`, and `NonePlusAnyoneCanPay` to the same `Prevouts::One` branch, then calls `taproot_key_spend_signature_hash` with the raw `sighash_type`. [3](#0-2) 

Per BIP-341, the ANYONECANPAY bit only affects which *input* prevout data (outpoint/amount/scriptPubKey/sequence of the input being spent) is committed — it says nothing about the destination output. The destination-output commitment is governed independently by the low two bits of the sighash type: `ALL` commits `sha_outputs` (hash of every transaction output, including the attacker-chosen `output_txout` at index 0), `SINGLE` commits only the output at the same index as the input, and `NONE` commits **no output data whatsoever**. The code comments/error message in `aggregator.rs:1126` ("Ensure the signature uses SinglePlusAnyoneCanPay") show the developers intended only `SinglePlusAnyoneCanPay` to be accepted, but nothing in the reachable code path rejects `AllPlusAnyoneCanPay` or `NonePlusAnyoneCanPay` before signature verification.

Exploit flow: the attacker (who controls the private key behind their own `withdrawal_utxo`, per the given threat model) signs the payout once using `NonePlusAnyoneCanPay`. Because `SIGHASH_NONE` excludes the outputs digest, this single signature is valid under `SECP.verify_schnorr` for *any* `output_amount`/`output_script_pubkey` the attacker later supplies in `OptimisticWithdrawParams` for the same `deposit_id`/`input_outpoint`, since the aggregator recomputes the sighash from its own freshly-built `output_txout` and the signature places no constraint on it. This lets the attacker request an inflated `output_amount` (or a different `output_script_pubkey`) than what was actually authorized/committed on the Citrea side, while still passing the aggregator's only cryptographic gate on this parameter.

### Impact Explanation
If the aggregator accepts this forged binding and proceeds to request N-of-N MuSig2 partial signatures from verifiers for the move-to-vault input (input 1) using the same inflated `output_txout`, and if the verifier-side signing path does not itself independently re-derive/verify the correct withdrawal amount from the Citrea light-client/storage proof before contributing its partial signature, the result is BTC leaving the move-to-vault UTXO in excess of (or to a destination different from) the amount actually fronted/authorized by the Citrea withdrawal event — the Critical category "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal." This is repeatable per withdrawal/deposit as long as the attacker can produce one `NonePlusAnyoneCanPay` signature over their own withdrawal UTXO.

I was not able to confirm, within the available tool budget, whether `core/src/verifier.rs`'s optimistic-payout signing path independently validates `output_amount`/`output_script_pubkey` against a Citrea storage proof (e.g., via `verify_storage_proofs`/`SPV::verify`) before contributing its MuSig2 partial signature for the move-to-vault input. If such a check exists and is enforced there, the blast radius is limited to the aggregator's own gate being bypassable without full fund loss; regardless, the aggregator-side check is broken as written and should not be relied upon as the binding control.

### Likelihood Explanation
No special privileges are required beyond what the stated threat model already grants: the attacker only needs to control the signing key for their own withdrawal UTXO and be able to call the aggregator's public `optimistic_payout` gRPC endpoint, both of which are explicitly in-scope attacker capabilities. Crafting a `NonePlusAnyoneCanPay` Schnorr signature is a standard, low-cost Bitcoin operation requiring no on-chain broadcast or fees to attempt against the aggregator.

### Recommendation
In `Aggregator::optimistic_payout`, explicitly reject any `input_signature.sighash_type` other than `TapSighashType::SinglePlusAnyoneCanPay` before calling `calculate_pubkey_spend_sighash`/`verify_schnorr`, e.g. add a check immediately after parsing `input_signature`:
```rust
if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
    return Err(Status::invalid_argument("input_signature must use SinglePlusAnyoneCanPay"));
}
```
Additionally, independently validate `output_amount`/`output_script_pubkey` against the authoritative Citrea withdrawal record (via light-client/storage proof) rather than trusting the signature alone as the sole binding mechanism, and ensure the verifier-side MuSig2 signing path for the move-to-vault input performs the same validation before contributing a partial signature.

### Proof of Concept
```rust
// core/src/test (illustrative outline; not a real test, out-of-scope dirs excluded from grading)
// 1. Build a withdrawal_utxo TxOut{value: V, script_pubkey: user_p2tr}.
// 2. Construct optimistic payout tx skeleton with output_txout = TxOut{value: 5000, script: A}.
// 3. Sign input 0 with TapSighashType::NonePlusAnyoneCanPay using the user's key -> sig_none.
// 4. Rebuild the tx with output_txout = TxOut{value: 6000, script: B} (different amount/script).
// 5. Recompute sighash via calculate_pubkey_spend_sighash(0, NonePlusAnyoneCanPay) on the NEW tx.
// 6. assert!(SECP.verify_schnorr(&sig_none, &new_message, &user_xonly_pk).is_ok());
//    -> demonstrates sig_none verifies against a different amount/script than originally intended.
// 7. Repeat steps 3-6 with SinglePlusAnyoneCanPay and AllPlusAnyoneCanPay:
//    assert!(SECP.verify_schnorr(...).is_err()) for both, confirming only NONE breaks the binding.
```

### Citations

**File:** core/src/rpc/aggregator.rs (L1095-1098)
```rust
            let output_txout = TxOut {
                value: output_amount,
                script_pubkey: output_script_pubkey,
            };
```

**File:** core/src/rpc/aggregator.rs (L1112-1126)
```rust
            let mut opt_payout_txhandler = create_optimistic_payout_txhandler(
                &mut deposit_data,
                withdrawal_utxo,
                output_txout,
                input_signature,
                self.config.protocol_paramset(),
            )?;

            let sighash = opt_payout_txhandler
                .calculate_pubkey_spend_sighash(0, input_signature.sighash_type)?;

            let message = Message::from_digest(sighash.to_byte_array());

            SECP.verify_schnorr(&input_signature.signature, &message, &user_xonly_pk)
                .map_err(|_| Status::internal("Invalid signature for optimistic payout tx. Ensure the signature uses SinglePlusAnyoneCanPay sighash type."))?;
```

**File:** core/src/builder/transaction/txhandler.rs (L222-233)
```rust
        let prevouts = match sighash_type {
            TapSighashType::SinglePlusAnyoneCanPay
            | TapSighashType::AllPlusAnyoneCanPay
            | TapSighashType::NonePlusAnyoneCanPay => {
                bitcoin::sighash::Prevouts::One(txin_index, prevouts_vec[txin_index])
            }
            _ => bitcoin::sighash::Prevouts::All(&prevouts_vec),
        };

        let sig_hash = sighash_cache
            .taproot_key_spend_signature_hash(txin_index, &prevouts, sighash_type)
            .wrap_err("Failed to calculate taproot sighash for key spend")?;
```
