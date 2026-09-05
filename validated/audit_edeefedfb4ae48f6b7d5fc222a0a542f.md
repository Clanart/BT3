## Finding

Duplicate public keys are never rejected when constructing a Stacks multisig (P2SH/P2WSH) spending condition, so a single co-signer contributing the same public key more than once can single-handedly satisfy an "M-of-N" signature threshold that the other participants believe requires cooperation of M *distinct* signers.

### Title
Missing Uniqueness Check on Public Keys in Multisig Address/Spending-Condition Construction Allows a Single Key to Satisfy an M-of-N Threshold - ([File: stacks-codec/src/transaction.rs], [File: stacks-common/src/types/mod.rs])

### Summary
`TransactionSpendingCondition::new_multisig_p2sh`, `new_multisig_p2wsh`, `new_multisig_order_independent_p2sh`, and `new_multisig_order_independent_p2wsh` build a multisig address directly from a caller-supplied `Vec<StacksPublicKey>` with no check that the keys are distinct [1](#0-0) . `StacksAddress::from_public_keys`, which they call, likewise performs no uniqueness validation before hashing the key list into the address [2](#0-1) . The underlying script-hash builder simply pushes each key (duplicates included) into the redeem script that is hashed to form the address [3](#0-2) .

### Finding Description
When an M-of-N multisig account is created, every participant is expected to contribute exactly one distinct public key so that moving funds genuinely requires cooperation of M independent private-key holders. Because no component in the creation path (`new_multisig_p2sh`/`new_multisig_order_independent_p2sh` → `StacksAddress::from_public_keys` → `to_bits_p2sh`/`to_bits_p2sh_p2wsh`) checks for duplicate keys, a malicious participant in the key-collection ceremony can submit the same public key multiple times among the N slots. The resulting address hash is still self-consistent (it simply encodes the key list, duplicates and all), so it passes every check later performed during spending.

At spend time, `MultisigSpendingCondition::verify()` and `OrderIndependentMultisigSpendingCondition::verify()` only check: (1) the count of supplied signatures equals/exceeds `signatures_required`, and (2) the recovered/explicit public keys hash to the stored `signer` via `StacksAddress::from_public_keys` [4](#0-3) [5](#0-4) . Neither function verifies that the signing public keys are pairwise distinct. If the address was built with a duplicated key, the holder of that one private key can supply that same key/signature pair in multiple field slots (for the order-independent variant this is trivial since the sighash is not chained across fields — each signature is produced over the same `initial_sighash`) and satisfy `signatures_required` alone, even though `signatures_required` was intended to represent that many *independent* signers.

This breaks the intended equality "number of distinct authorizing signers ≥ signatures_required" — the check that actually executes is merely "number of signature fields ≥ signatures_required" combined with "the (possibly duplicated) key list hashes to the stored address," which a duplicate-key address always satisfies for its sole colluding/malicious key holder.

### Impact Explanation
This is a High/Critical-class issue matching "signatures verified fewer than the threshold" and, once the wallet is funded by the honest co-signers under the false assumption of M-distinct-signer protection, "an asset moving without authorization" from the other participants' perspective. A minority (even a single) malicious participant in a supposedly M-of-N Stacks multisig wallet can unilaterally move all funds, defeating the entire purpose of the multisig scheme, without ever needing the other signers' keys.

### Likelihood Explanation
Exploitation requires that the malicious party control the key-collection/address-generation step of the multisig setup (i.e., they are one of the N contributing signers), which is a realistic role for a participant to legitimately hold in any real-world M-of-N wallet setup — this is not a privileged network-admin or miner role. Since no code path anywhere from `new_multisig_p2sh`/`new_multisig_order_independent_p2sh` through `verify()` rejects duplicate keys, the bug is deterministically exploitable by any dishonest co-signer at wallet-creation time.

### Recommendation
Add a uniqueness check on the `pubkeys` vector in `StacksAddress::from_public_keys` (and/or in `TransactionSpendingCondition::new_multisig_p2sh` / `new_multisig_p2wsh` / `new_multisig_order_independent_p2sh` / `new_multisig_order_independent_p2wsh`), rejecting address construction if any two entries are identical. Additionally, `MultisigSpendingCondition::verify()` and `OrderIndependentMultisigSpendingCondition::verify()` should independently reject spending conditions whose recovered/explicit `pubkeys` list contains duplicates, so that even pre-existing (already-created) duplicate-key addresses cannot be spent by fewer than the intended number of distinct signers.

### Proof of Concept
1. Three parties agree to create a 2-of-3 P2SH multisig wallet via `TransactionSpendingCondition::new_multisig_p2sh(2, vec![pubkey_A, pubkey_B, pubkey_C])` [6](#0-5) .
2. Malicious party A instead submits `vec![pubkey_A, pubkey_A, pubkey_C]` (their own key twice, omitting B's key) during setup; no code rejects this, and `StacksAddress::from_public_keys` happily returns a valid address [2](#0-1) .
3. Honest party C funds this address believing 2 of {A, B, C} must cooperate.
4. Party A alone signs the same `initial_sighash` twice with `privkey_A`, producing two `TransactionAuthField::Signature` entries with the recovered key `pubkey_A` appearing in both slots that the address expects.
5. `MultisigSpendingCondition::verify()` counts `num_sigs == 2 == signatures_required`, recomputes `addr` from `[pubkey_A, pubkey_A, pubkey_C]`, finds it equals `self.signer`, and accepts the transaction [7](#0-6)  — moving funds with only party A's cooperation, defeating the 2-of-3 guarantee relied upon by party C.

### Citations

**File:** stacks-codec/src/transaction.rs (L909-982)
```rust
    pub fn verify(
        &self,
        initial_sighash: &Txid,
        cond_code: &TransactionAuthFlags,
        mode: TransactionAuthVerificationMode,
    ) -> Result<Txid, AuthError> {
        let mut pubkeys = vec![];
        let mut cur_sighash = initial_sighash.clone();
        let mut num_sigs: u16 = 0;
        let mut have_uncompressed = false;
        for field in self.fields.iter() {
            let pubkey = match field {
                TransactionAuthField::PublicKey(ref pubkey) => {
                    if !pubkey.compressed() {
                        have_uncompressed = true;
                    }
                    pubkey.clone()
                }
                TransactionAuthField::Signature(ref pubkey_encoding, ref sigbuf) => {
                    if *pubkey_encoding == TransactionPublicKeyEncoding::Uncompressed {
                        have_uncompressed = true;
                    }

                    let (pubkey, next_sighash) = TransactionSpendingCondition::next_verification(
                        &cur_sighash,
                        cond_code,
                        self.tx_fee,
                        self.nonce,
                        pubkey_encoding,
                        sigbuf,
                        mode,
                    )?;
                    cur_sighash = next_sighash;
                    num_sigs = num_sigs
                        .checked_add(1)
                        .ok_or(AuthError::VerifyingError("Too many signatures".to_string()))?;
                    pubkey
                }
            };
            pubkeys.push(pubkey);
        }

        if num_sigs != self.signatures_required {
            return Err(AuthError::VerifyingError(
                "Incorrect number of signatures".to_string(),
            ));
        }

        if have_uncompressed && self.hash_mode == MultisigHashMode::P2WSH {
            return Err(AuthError::VerifyingError(
                "Uncompressed keys are not allowed in this hash mode".to_string(),
            ));
        }

        let addr = StacksAddress::from_public_keys(
            0,
            &self.hash_mode.to_address_hash_mode(),
            self.signatures_required as usize,
            &pubkeys,
        )
        .ok_or_else(|| {
            AuthError::VerifyingError("Failed to generate address from public keys".to_string())
        })?;

        if addr.bytes() != &self.signer {
            return Err(AuthError::VerifyingError(format!(
                "Signer hash does not equal hash of public key(s): {} != {}",
                addr.bytes(),
                self.signer
            )));
        }

        Ok(cur_sighash)
    }
```

**File:** stacks-codec/src/transaction.rs (L1099-1171)
```rust
    pub fn verify(
        &self,
        initial_sighash: &Txid,
        cond_code: &TransactionAuthFlags,
        mode: TransactionAuthVerificationMode,
    ) -> Result<Txid, AuthError> {
        let mut pubkeys = vec![];
        let mut num_sigs: u16 = 0;
        let mut have_uncompressed = false;
        for field in self.fields.iter() {
            let pubkey = match field {
                TransactionAuthField::PublicKey(ref pubkey) => {
                    if !pubkey.compressed() {
                        have_uncompressed = true;
                    }
                    pubkey.clone()
                }
                TransactionAuthField::Signature(ref pubkey_encoding, ref sigbuf) => {
                    if *pubkey_encoding == TransactionPublicKeyEncoding::Uncompressed {
                        have_uncompressed = true;
                    }

                    let (pubkey, _next_sighash) = TransactionSpendingCondition::next_verification(
                        initial_sighash,
                        cond_code,
                        self.tx_fee,
                        self.nonce,
                        pubkey_encoding,
                        sigbuf,
                        mode,
                    )?;
                    num_sigs = num_sigs
                        .checked_add(1)
                        .ok_or(AuthError::VerifyingError("Too many signatures".to_string()))?;
                    pubkey
                }
            };
            pubkeys.push(pubkey);
        }

        if num_sigs < self.signatures_required {
            return Err(AuthError::VerifyingError(format!(
                "Not enough signatures. Got {num_sigs}, expected at least {req}",
                req = self.signatures_required
            )));
        }

        if have_uncompressed && self.hash_mode == OrderIndependentMultisigHashMode::P2WSH {
            return Err(AuthError::VerifyingError(
                "Uncompressed keys are not allowed in this hash mode".to_string(),
            ));
        }

        let addr = StacksAddress::from_public_keys(
            0,
            &self.hash_mode.to_address_hash_mode(),
            self.signatures_required as usize,
            &pubkeys,
        )
        .ok_or_else(|| {
            AuthError::VerifyingError("Failed to generate address from public keys".to_string())
        })?;

        if addr.bytes() != &self.signer {
            return Err(AuthError::VerifyingError(format!(
                "Signer hash does not equal hash of public key(s): {} != {}",
                addr.bytes(),
                self.signer
            )));
        }

        Ok(initial_sighash.clone())
    }
```

**File:** stacks-codec/src/transaction.rs (L1392-1459)
```rust
    pub fn new_multisig_p2sh(
        num_sigs: u16,
        pubkeys: Vec<StacksPublicKey>,
    ) -> Option<TransactionSpendingCondition> {
        let signer_addr = StacksAddress::from_public_keys(
            0,
            &AddressHashMode::SerializeP2SH,
            usize::from(num_sigs),
            &pubkeys,
        )?;

        Some(TransactionSpendingCondition::Multisig(
            MultisigSpendingCondition {
                signer: signer_addr.destruct().1,
                nonce: 0,
                tx_fee: 0,
                hash_mode: MultisigHashMode::P2SH,
                fields: vec![],
                signatures_required: num_sigs,
            },
        ))
    }

    pub fn new_multisig_order_independent_p2sh(
        num_sigs: u16,
        pubkeys: Vec<StacksPublicKey>,
    ) -> Option<TransactionSpendingCondition> {
        let signer_addr = StacksAddress::from_public_keys(
            0,
            &AddressHashMode::SerializeP2SH,
            usize::from(num_sigs),
            &pubkeys,
        )?;

        Some(TransactionSpendingCondition::OrderIndependentMultisig(
            OrderIndependentMultisigSpendingCondition {
                signer: signer_addr.destruct().1,
                nonce: 0,
                tx_fee: 0,
                hash_mode: OrderIndependentMultisigHashMode::P2SH,
                fields: vec![],
                signatures_required: num_sigs,
            },
        ))
    }

    pub fn new_multisig_order_independent_p2wsh(
        num_sigs: u16,
        pubkeys: Vec<StacksPublicKey>,
    ) -> Option<TransactionSpendingCondition> {
        let signer_addr = StacksAddress::from_public_keys(
            0,
            &AddressHashMode::SerializeP2WSH,
            usize::from(num_sigs),
            &pubkeys,
        )?;

        Some(TransactionSpendingCondition::OrderIndependentMultisig(
            OrderIndependentMultisigSpendingCondition {
                signer: signer_addr.destruct().1,
                nonce: 0,
                tx_fee: 0,
                hash_mode: OrderIndependentMultisigHashMode::P2WSH,
                fields: vec![],
                signatures_required: num_sigs,
            },
        ))
    }
```

**File:** stacks-common/src/types/mod.rs (L1040-1075)
```rust
    pub fn from_public_keys(
        version: u8,
        hash_mode: &AddressHashMode,
        num_sigs: usize,
        pubkeys: &Vec<StacksPublicKey>,
    ) -> Option<StacksAddress> {
        // must be sufficient public keys
        if pubkeys.len() < num_sigs {
            return None;
        }

        // address hash mode must be consistent with the number of keys
        match *hash_mode {
            AddressHashMode::SerializeP2PKH | AddressHashMode::SerializeP2WPKH
                // must be a single public key, and must require one signature
                if (num_sigs != 1 || pubkeys.len() != 1) => {
                    return None;
                }
            _ => {}
        }

        // if segwit, then keys must all be compressed
        match *hash_mode {
            AddressHashMode::SerializeP2WPKH | AddressHashMode::SerializeP2WSH => {
                for pubkey in pubkeys {
                    if !pubkey.compressed() {
                        return None;
                    }
                }
            }
            _ => {}
        }

        let hash_bits = public_keys_to_address_hash(hash_mode, num_sigs, pubkeys);
        StacksAddress::new(version, hash_bits).ok()
    }
```

**File:** stacks-common/src/address/mod.rs (L157-168)
```rust
fn to_bits_p2sh<K: PublicKey>(num_sigs: usize, pubkeys: &Vec<K>) -> Hash160 {
    let mut bldr = Builder::new();
    bldr = bldr.push_int(num_sigs as i64);
    for pubk in pubkeys {
        bldr = bldr.push_slice(&pubk.to_bytes());
    }
    bldr = bldr.push_int(pubkeys.len() as i64);
    bldr = bldr.push_opcode(btc_opcodes::OP_CHECKMULTISIG);

    let script = bldr.into_script();
    Hash160::from_data(script.as_bytes())
}
```
