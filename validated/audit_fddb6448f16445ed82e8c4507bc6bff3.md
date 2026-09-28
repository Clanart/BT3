### Title
OP_RETURN data cap of 80 bytes in `SignableTransaction::new` disagrees with the protocol-level `MAX_DATA_LEN`, bricking any batch containing an in-range payment - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` hard-rejects any `data` payload longer than 80 bytes (`data.as_ref().map_or(0, Vec::len) > 80` → `TransactionError::TooMuchData`), while the Serai protocol accepts transaction `data` up to `MAX_DATA_LEN` (defined in `serai_client::primitives`, imported at `processor/src/networks/bitcoin.rs:45`). Any Bitcoin payment carrying data in the gap `(80, MAX_DATA_LEN]` is valid per the protocol but can never be turned into a signable transaction.

### Finding Description [1](#0-0) 

The constructor enforces a literal `80`-byte ceiling on the OP_RETURN payload, citing Bitcoin's standardness limit, and then relies on it via `PushBytesBuf::try_from(data).expect("data didn't fit into PushBytes depsite being checked")`: [2](#0-1) 

However, the processor imports `MAX_DATA_LEN` from `serai_client::primitives` — the consensus-level bound on `Transaction::Bitcoin` `data` — and forwards user-supplied data to `SignableTransaction::new` without reconciling the two limits: [3](#0-2) 

`MAX_DATA_LEN` is larger than 80 (512 bytes), so the set of data lengths accepted on-chain strictly contains the set the wallet can encode. This is the same bug class as the report: a documented/declared cap that does not match the enforced constant, except here the mismatch is exploitable rather than merely cosmetic.

### Impact Explanation
An unprivileged user submits a Serai `Transaction::Bitcoin` payment with `data` of 81–512 bytes. This passes substrate validation (≤ `MAX_DATA_LEN`) and is scheduled for signing, but every attempt to construct the `SignableTransaction` returns `TransactionError::TooMuchData`. The batch can never be completed, stalling the scheduler's output queue — user funds committed to that batch remain unspendable until intervention, and the scheduler may wedge on the unbuildable batch. This is a reachable liveness/funds-availability failure caused purely by attacker-supplied transaction bytes.

### Likelihood Explanation
Fully public input path: anyone can submit a Bitcoin payment with arbitrary `data` up to the protocol limit. No validator compromise, timing, or collusion needed — a single transaction with 81 bytes of data triggers it deterministically. Rated Medium: it is a permanent-ish stall of affected payments rather than theft or forgery.

### Recommendation
Either lower `MAX_DATA_LEN` for Bitcoin-bound `data` to 80 at the protocol level, or make `SignableTransaction::new` use the same constant (`serai_client::primitives::MAX_DATA_LEN`) and/or split oversized data across multiple `OP_RETURN` pushes (`PushBytesBuf` supports repeated pushes) so the documented cap is actually encodable.

### Proof of Concept
1. Submit a valid Serai transaction requesting a Bitcoin payment with `data = vec![0u8; 100]` (≤ `MAX_DATA_LEN` = 512, > 80).
2. Substrate accepts and emits it; the scheduler selects it for a batch.
3. `SignableTransaction::new(inputs, payments, change, Some(data), fee)` hits `data.len() > 80` at `send.rs:171` and returns `TransactionError::TooMuchData`.
4. No `SignableTransaction` exists to sign; the batch — and any payments queued behind it — can never be produced or spent.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L171-173)
```rust
    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L194-201)
```rust
    if let Some(data) = data {
      tx_outs.push(TxOut {
        value: Amount::ZERO,
        script_pubkey: ScriptBuf::new_op_return(
          PushBytesBuf::try_from(data)
            .expect("data didn't fit into PushBytes depsite being checked"),
        ),
      })
```

**File:** processor/src/networks/bitcoin.rs (L44-47)
```rust
use serai_client::{
  primitives::{MAX_DATA_LEN, ExternalCoin, ExternalNetworkId, Amount, ExternalBalance},
  networks::bitcoin::Address,
};
```
