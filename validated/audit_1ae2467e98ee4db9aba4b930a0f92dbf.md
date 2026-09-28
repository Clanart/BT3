### Title
Unauthenticated `SignCompleted` transaction lets any party forge completion of a signing plan with an arbitrary `tx_hash` - ([File: coordinator/src/tributary/transaction.rs](coordinator/src/tributary/transaction.rs))

### Summary
CVE-2021-41280 is an unauthenticated-reach bug: a security parameter (`sns_notification_token`) defaults to unset, so attacker-controlled bytes reach a dangerous sink without authentication. The Serai analog is `Transaction::SignCompleted`: it is classified as `TransactionKind::Unsigned`, so `verify_transaction` performs no signer-membership or signature check on it, and its only authentication is an embedded self-signed `first_signer`/`signature` pair — both fully attacker-chosen. Any untrusted party who can submit a tributary transaction can forge a `SignCompleted` for any `plan` with any `tx_hash`.

### Finding Description
`Transaction::SignCompleted` returns `TransactionKind::Unsigned` [1](#0-0) . In `verify_transaction`, `Provided` and `Unsigned` kinds skip the signer/nonce/signature check entirely [2](#0-1) . The mempool's `add` likewise only calls `app_tx.verify()` for `Unsigned` transactions [3](#0-2) .

The only authentication is in `Transaction::verify`, which checks `signature.verify(*first_signer, self.sign_completed_challenge())` [4](#0-3) . But `first_signer` is read directly from attacker-controlled bytes in `Transaction::read` [5](#0-4) , and the challenge binds `plan`, `tx_hash`, `first_signer`, and `R` — all chosen by the attacker [6](#0-5) . There is no check anywhere that `first_signer` is a validator or participant in the signing set. This is exactly the "unset token" shape: the field that should authenticate the message is attacker-supplied, so the signature is a signature by the attacker attesting to the attacker's own claim — i.e., no authentication at all.

Additionally, `SignCompleted` is deliberately de-duplicated ("the signer of the first TX to be included with this pairing will be remembered on-chain") [7](#0-6) , so a forged copy can race and displace the legitimate report.

### Impact Explanation
A forged `SignCompleted` causes the coordinator/processors to treat a signing plan as completed with an attacker-chosen `tx_hash` — a transaction hash for a transaction that was never produced or broadcast. This reports a transfer as completed that does not exist on the external chain ("funds reported received/sent that are not spendable"), and because the honest `SignCompleted` is deduplicated against the forged one, it can also permanently suppress the true completion report for that plan/attempt, hanging or mis-resolving the signing session. Impact is reached purely from bytes the attacker serializes into `Transaction::SignCompleted` and submits to the tributary mempool.

### Likelihood Explanation
Reachability requires only the ability to submit a tributary transaction (an unprivileged gossip/RPC input), a Ristretto keypair the attacker controls, and a single Schnorr signature over `sign_completed_challenge`. No validator status, threshold participation, or secret knowledge is needed. Likelihood is bounded by whether the attacker can win the inclusion race against the legitimate reporter and by whatever downstream consumers do with the forged `tx_hash`, which is why this is not Critical.

### Recommendation
Treat `SignCompleted` like the signed transactions: either change it to `TransactionKind::Signed` so `verify_transaction` enforces signer membership and nonce, or in `Transaction::verify` explicitly check that `first_signer` belongs to the validator set / signing participants for `plan` before accepting the signature. The signature must bind to a *known authorized* signer, not to a signer supplied inside the unauthenticated message.

### Proof of Concept
1. Generate an arbitrary scalar `k`, compute `first_signer = G * k`.
2. Pick any `plan: [u8; 32]` (an in-flight signing plan ID) and a fabricated `tx_hash` (e.g., 32 zero bytes).
3. Choose nonce `r`, set `R = G * r`, compute `c = sign_completed_challenge(plan, tx_hash, first_signer, R)` per lines 703-715, and `s = r + c*k`.
4. Serialize `Transaction::SignCompleted { plan, tx_hash, first_signer, signature: SchnorrSignature { R, s } }` (kind byte `10`) and submit it to the tributary mempool.
5. `Mempool::add` → `verify_transaction` → kind is `Unsigned` → only `tx.verify()` runs → `signature.verify(first_signer, challenge)` succeeds because the attacker controls `k`. The forged completion is accepted into the pool and can be included on-chain before the legitimate `SignCompleted`, which is then deduplicated away.

### Citations

**File:** coordinator/src/tributary/transaction.rs (L182-187)
```rust
  // This is defined as an Unsigned transaction in order to de-duplicate SignCompleted amongst
  // reporters (who should all report the same thing)
  // We do still track the signer in order to prevent a single signer from publishing arbitrarily
  // many TXs without penalty
  // Here, they're denoted as the first_signer, as only the signer of the first TX to be included
  // with this pairing will be remembered on-chain
```

**File:** coordinator/src/tributary/transaction.rs (L409-421)
```rust
      10 => {
        let mut plan = [0; 32];
        reader.read_exact(&mut plan)?;

        let mut tx_hash_len = [0];
        reader.read_exact(&mut tx_hash_len)?;
        let mut tx_hash = vec![0; usize::from(tx_hash_len[0])];
        reader.read_exact(&mut tx_hash)?;

        let first_signer = Ristretto::read_G(reader)?;
        let signature = SchnorrSignature::<Ristretto>::read(reader)?;

        Ok(Transaction::SignCompleted { plan, tx_hash, first_signer, signature })
```

**File:** coordinator/src/tributary/transaction.rs (L596-596)
```rust
      Transaction::SignCompleted { .. } => TransactionKind::Unsigned,
```

**File:** coordinator/src/tributary/transaction.rs (L616-620)
```rust
    if let Transaction::SignCompleted { first_signer, signature, .. } = self {
      if !signature.verify(*first_signer, self.sign_completed_challenge()) {
        Err(TransactionError::InvalidContent)?;
      }
    }
```

**File:** coordinator/src/tributary/transaction.rs (L703-715)
```rust
  pub fn sign_completed_challenge(&self) -> <Ristretto as Ciphersuite>::F {
    if let Transaction::SignCompleted { plan, tx_hash, first_signer, signature } = self {
      let mut transcript =
        RecommendedTranscript::new(b"Coordinator Tributary Transaction SignCompleted");
      transcript.append_message(b"plan", plan);
      transcript.append_message(b"tx_hash", tx_hash);
      transcript.append_message(b"signer", first_signer.to_bytes());
      transcript.append_message(b"nonce", signature.R.to_bytes());
      Ristretto::hash_to_F(b"SignCompleted signature", &transcript.challenge(b"challenge"))
    } else {
      panic!("sign_completed_challenge called on transaction which wasn't SignCompleted")
    }
  }
```

**File:** coordinator/tributary/src/transaction.rs (L199-215)
```rust
  match tx.kind() {
    TransactionKind::Provided(_) | TransactionKind::Unsigned => {}
    TransactionKind::Signed(order, Signed { signer, nonce, signature }) => {
      if let Some(next_nonce) = get_and_increment_nonce(signer, &order) {
        if *nonce != next_nonce {
          Err(TransactionError::InvalidNonce)?;
        }
      } else {
        // Not a participant
        Err(TransactionError::InvalidSigner)?;
      }

      // TODO: Use a batch verification here
      if !signature.verify(*signer, tx.sig_hash(genesis)) {
        Err(TransactionError::InvalidSignature)?;
      }
    }
```

**File:** coordinator/tributary/src/mempool.rs (L161-168)
```rust
          TransactionKind::Unsigned => {
            // check we have the tx in the pool/chain
            if self.unsigned_already_exist(tx.hash(), unsigned_in_chain) {
              return Ok(false);
            }

            app_tx.verify()?;
          }
```
