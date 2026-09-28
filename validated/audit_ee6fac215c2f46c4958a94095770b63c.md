### Title
Unvalidated Bitcoin payment scripts allow threshold funds to be signed to unspendable or anyone-can-spend destinations - (networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` accepts each payment destination as an unrestricted `ScriptBuf` and only validates the payment amount. It copies the supplied script directly into a transaction output and later produces a Taproot signature committing to all outputs. An empty or otherwise unspendable script can therefore receive multisig funds.

### Finding Description
`SignableTransaction::new` receives `payments: &[(ScriptBuf, u64)]` and checks only that every amount is at least `DUST`; it performs no emptiness, standardness, or spendability check on the destination script. [1](#0-0)  The supplied `ScriptBuf` is then cloned directly into the corresponding `TxOut`. [2](#0-1)  `TransactionSignMachine::sign` computes a Taproot signature hash over the full transaction and creates a signature share without revalidating the destination. [3](#0-2) 

### Impact Explanation
A transaction can transfer threshold-controlled funds to an empty `script_pubkey`, which is anyone-can-spend under Bitcoin script semantics, or to a syntactically valid output with an impossible key/hash, making the funds permanently inaccessible. Because the generated signature commits to the malicious or malformed output, the signed transaction is valid evidence of the unintended transfer rather than a signing failure. [2](#0-1) [4](#0-3) 

### Likelihood Explanation
An unprivileged caller that can influence the payment destination supplied to the wallet/signing API can provide an empty or invalid script and have it included without rejection. No private-key material, validator privilege, malformed UTXO, or signing-protocol manipulation is required. [1](#0-0) [5](#0-4) 

### Recommendation
Reject empty payment and change scripts and restrict externally supplied destinations to the supported standard address forms before constructing the transaction. The validation should occur in `SignableTransaction::new`, so lower-level signing machines cannot commit to a destination rejected by wallet policy. [1](#0-0) 

### Proof of Concept
```rust
use bitcoin_serai::bitcoin::ScriptBuf;
use bitcoin_serai::wallet::SignableTransaction;

let spendable_input: ReceivedOutput = scanned_wallet_output;
let tx = SignableTransaction::new(
  vec![spendable_input],
  &[(ScriptBuf::new(), 10_000)],
  None,
  None,
  1,
).unwrap();
```

`SignableTransaction::new` accepts the empty destination because no destination-script check exists, creates an output containing that script, and `TransactionSignMachine::sign` then signs the transaction committing to it. [1](#0-0) [2](#0-1) [6](#0-5)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L150-168)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
    if inputs.is_empty() {
      Err(TransactionError::NoInputs)?;
    }

    if payments.is_empty() && change.is_none() && data.is_none() {
      Err(TransactionError::NoOutputs)?;
    }

    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
```

**File:** networks/bitcoin/src/wallet/send.rs (L187-191)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L355-397)
```rust
  fn sign(
    mut self,
    commitments: HashMap<Participant, Self::Preprocess>,
    msg: &[u8],
  ) -> Result<(TransactionSignatureMachine, Self::SignatureShare), FrostError> {
    if !msg.is_empty() {
      panic!("message was passed to the TransactionSignMachine when it generates its own");
    }

    let commitments = (0 .. self.sigs.len())
      .map(|c| {
        commitments
          .iter()
          .map(|(l, commitments)| (*l, commitments[c].clone()))
          .collect::<HashMap<_, _>>()
      })
      .collect::<Vec<_>>();

    let mut cache = SighashCache::new(&self.tx.tx);
    // Sign committing to all inputs
    let prevouts = Prevouts::All(&self.tx.prevouts);

    let mut shares = Vec::with_capacity(self.sigs.len());
    let sigs = self
      .sigs
      .drain(..)
      .enumerate()
      .map(|(i, sig)| {
        let (sig, share) = sig.sign(
          commitments[i].clone(),
          cache
            .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
            // This should never happen since the inputs align with the TX the cache was
            // constructed with, and because i is always < prevouts.len()
            .expect("taproot_key_spend_signature_hash failed to return a hash")
            .as_ref(),
        )?;
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
```
