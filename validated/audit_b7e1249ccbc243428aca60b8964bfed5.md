[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** stacks-codec/src/transaction.rs (L1707-1731)
```rust
    pub fn next_signature(
        cur_sighash: &Txid,
        cond_code: &TransactionAuthFlags,
        tx_fee: u64,
        nonce: u64,
        privk: &StacksPrivateKey,
    ) -> Result<(MessageSignature, Txid), AuthError> {
        let sighash_presign = TransactionSpendingCondition::make_sighash_presign(
            cur_sighash,
            cond_code,
            tx_fee,
            nonce,
        );

        // sign the current hash
        let sig = privk
            .sign(sighash_presign.as_bytes())
            .map_err(|se| AuthError::SigningError(se.to_string()))?;

        let pubk = StacksPublicKey::from_private(privk);
        let next_sighash =
            TransactionSpendingCondition::make_sighash_postsign(&sighash_presign, &pubk, &sig);

        Ok((sig, next_sighash))
    }
```

**File:** stacks-codec/src/transaction.rs (L1737-1774)
```rust
    pub fn next_verification(
        cur_sighash: &Txid,
        cond_code: &TransactionAuthFlags,
        tx_fee: u64,
        nonce: u64,
        key_encoding: &TransactionPublicKeyEncoding,
        sig: &MessageSignature,
        mode: TransactionAuthVerificationMode,
    ) -> Result<(StacksPublicKey, Txid), AuthError> {
        let sighash_presign = TransactionSpendingCondition::make_sighash_presign(
            cur_sighash,
            cond_code,
            tx_fee,
            nonce,
        );

        // verify the current signature
        let pubk = if mode == TransactionAuthVerificationMode::AllowHighS {
            StacksPublicKey::recover_to_pubkey_without_validating_low_s(
                sighash_presign.as_bytes(),
                sig,
            )
        } else {
            StacksPublicKey::recover_to_pubkey(sighash_presign.as_bytes(), sig)
        };

        let mut pubk = pubk.map_err(|ve| AuthError::VerifyingError(ve.to_string()))?;

        match key_encoding {
            TransactionPublicKeyEncoding::Compressed => pubk.set_compressed(true),
            TransactionPublicKeyEncoding::Uncompressed => pubk.set_compressed(false),
        };

        // what's the next sighash going to be?
        let next_sighash =
            TransactionSpendingCondition::make_sighash_postsign(&sighash_presign, &pubk, sig);
        Ok((pubk, next_sighash))
    }
```

**File:** stacks-codec/src/transaction.rs (L2011-2038)
```rust
    pub fn verify_origin(
        &self,
        initial_sighash: &Txid,
        mode: TransactionAuthVerificationMode,
    ) -> Result<Txid, AuthError> {
        match *self {
            TransactionAuth::Standard(ref origin_condition) => {
                origin_condition.verify(initial_sighash, &TransactionAuthFlags::AuthStandard, mode)
            }
            TransactionAuth::Sponsored(ref origin_condition, _) => {
                origin_condition.verify(initial_sighash, &TransactionAuthFlags::AuthStandard, mode)
            }
        }
    }

    pub fn verify(
        &self,
        initial_sighash: &Txid,
        mode: TransactionAuthVerificationMode,
    ) -> Result<(), AuthError> {
        let origin_sighash = self.verify_origin(initial_sighash, mode)?;
        match *self {
            TransactionAuth::Standard(_) => Ok(()),
            TransactionAuth::Sponsored(_, ref sponsor_condition) => sponsor_condition
                .verify(&origin_sighash, &TransactionAuthFlags::AuthSponsored, mode)
                .map(|_sigh| ()),
        }
    }
```

**File:** stacks-codec/src/transaction.rs (L3241-3253)
```rust
    pub fn sign_begin(&self) -> Txid {
        let mut tx = self.clone();
        tx.auth = tx.auth.into_initial_sighash_auth();
        tx.txid()
    }

    /// begin verifying a transaction.
    /// return the initial sighash
    pub fn verify_begin(&self) -> Txid {
        let mut tx = self.clone();
        tx.auth = tx.auth.into_initial_sighash_auth();
        tx.txid()
    }
```

**File:** stacks-codec/src/transaction.rs (L3392-3401)
```rust
    /// Verify this transaction's signatures
    pub fn verify(&self, mode: TransactionAuthVerificationMode) -> Result<(), AuthError> {
        self.auth.verify(&self.verify_begin(), mode)
    }

    /// Verify the transaction's origin signatures only.
    /// Used by sponsors to get the next sig-hash to sign.
    pub fn verify_origin(&self, mode: TransactionAuthVerificationMode) -> Result<Txid, AuthError> {
        self.auth.verify_origin(&self.verify_begin(), mode)
    }
```
