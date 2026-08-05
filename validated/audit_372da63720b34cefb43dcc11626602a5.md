[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** entry/src/entry.rs (L139-146)
```rust
pub fn batch_verify<'a, I>(items: I) -> bool
where
    I: IntoParallelIterator<Item = (&'a Signature, &'a Address, &'a [u8])>,
{
    items
        .into_par_iter()
        .all(|(signature, pubkey, message)| signature.verify(pubkey.as_ref(), message))
}
```

**File:** entry/src/entry.rs (L159-165)
```rust
    pub fn verify(&self) -> Result<()> {
        let verification_items = self.signatures.par_iter().flat_map_iter(|tx| {
            let message = tx.serialized_message.as_slice();
            let len = tx.signatures.len();

            (0..len).map(move |i| (&tx.signatures[i], &tx.signer_pubkeys[i], message))
        });
```

**File:** entry/src/entry.rs (L167-171)
```rust
        if batch_verify(verification_items) {
            Ok(())
        } else {
            Err(TransactionError::SignatureFailure)
        }
```

**File:** entry/src/entry.rs (L345-352)
```rust
            let num_signers = usize::from(versioned_tx.message.header().num_required_signatures);
            let static_account_keys = versioned_tx.message.static_account_keys();
            if static_account_keys.len() < num_signers {
                return Err(TransactionError::SanitizeFailure);
            }
            let signatures = versioned_tx.signatures.iter().copied().collect();
            let signer_pubkeys = static_account_keys[..num_signers].iter().copied().collect();
            let serialized_message = versioned_tx.message.serialize();
```

**File:** entry/src/entry.rs (L353-361)
```rust
            let verified_transaction = verify(versioned_tx, &serialized_message)?;
            let message_hash = *verified_transaction.message_hash();
            unverified_signatures.signatures.push(TxVerificationData {
                is_simple_vote: verified_transaction.is_simple_vote_transaction(),
                signatures,
                message_hash,
                serialized_message,
                signer_pubkeys,
            });
```
