### Title
Zero-value `signatures_required` in multisig spending conditions bypasses signature authentication - ([File: stacks-codec/src/transaction.rs], [File: stacks-common/src/types/mod.rs])

### Summary
Neither `MultisigSpendingCondition::verify`/`OrderIndependentMultisigSpendingCondition::verify` nor the underlying `StacksAddress::from_public_keys` reject a `signatures_required` value of `0`. This missing zero-check (the same bug class as the reported PieDao issue, where an unchecked parameter reaching zero disables a control) lets an attacker construct a spending condition that authenticates with **zero signatures and zero public keys**, deriving a fixed, precomputable address. Any funds ever associated with that deterministic "0-of-0" address can be spent by anyone, without any valid signature.

### Finding Description
`MultisigSpendingCondition` and `OrderIndependentMultisigSpendingCondition` carry a `signatures_required: u16` field that is read directly off the wire during `consensus_deserialize` with no lower-bound check [1](#0-0) . The same absence of a minimum check exists in the order-independent variant's deserializer [2](#0-1) .

During authentication, `verify()` iterates over `self.fields` (which may be empty) and only compares the *count* of supplied signatures/pubkeys against `signatures_required`:
- For `MultisigSpendingCondition::verify`, the check is `num_sigs != self.signatures_required` [3](#0-2) .
- For `OrderIndependentMultisigSpendingCondition::verify`, the check is `num_sigs < self.signatures_required` [4](#0-3) .

If `signatures_required == 0` and `fields` is empty, both checks pass trivially with `num_sigs == 0` and `pubkeys == []`. The code then calls `StacksAddress::from_public_keys(0, hash_mode, 0, &[])` [5](#0-4) [6](#0-5) .

`StacksAddress::from_public_keys` only validates that `pubkeys.len() >= num_sigs` (`0 >= 0` passes) and validates P2PKH/P2WPKH single-key requirements, but never rejects `num_sigs == 0` for the P2SH/P2WSH branches [7](#0-6) . It then calls `public_keys_to_address_hash(hash_mode, 0, &[])`, which for P2SH/P2WSH builds a Bitcoin-style script hash from an empty key list and `num_sigs = 0` [8](#0-7) . This produces one specific, deterministic `Hash160` for each hash mode (P2SH-0-of-0 and P2WSH-0-of-0) that anyone can precompute offline.

Because the resulting address is fixed and publicly computable, an attacker only needs to set `self.signer` to that hash to pass the final check `addr.bytes() != &self.signer` [9](#0-8) [10](#0-9) . The transaction is then treated as fully authenticated (`verify` returns `Ok`) despite containing zero signatures — breaking the fundamental equality that spending authorization requires at least one valid signature from the claimed owner.

### Impact Explanation
This falls under "asset moving without authorization / forging a transaction so it moves without a valid signature." Any STX (or any asset governed by this spending-condition scheme, e.g. via a multisig account) held at the deterministic 0-of-0 P2SH/P2WSH address can be spent by an unprivileged attacker who has never held any private key for it, since a `MultisigSpendingCondition`/`OrderIndependentMultisigSpendingCondition` with `signatures_required = 0` and empty `fields` will authenticate successfully against that fixed signer hash. This qualifies as Critical: forging a transaction so an asset moves without authorization.

### Likelihood Explanation
Likelihood of exploitation depends on whether funds are ever sent to (or accrue nonce/authorization at) the specific precomputed 0-of-0 address for a given hash mode — this is analogous to a burn-address style edge case rather than an arbitrary account takeover, since the attacker cannot choose the target address; it is fixed by the hash construction. However, the address is fully deterministic and requires no coordination to discover, so any accidental or intentional STX/asset transfer to it is immediately and permanently stealable by anyone, with no signature needed at all.

### Recommendation
Reject `signatures_required == 0` during deserialization of `MultisigSpendingCondition` and `OrderIndependentMultisigSpendingCondition` (mirroring the wire-format validation already done for `num_sigs_given != signatures_required`), and additionally have `StacksAddress::from_public_keys` return `None` when `num_sigs == 0` for the P2SH/P2WSH hash modes, closing off the ability to construct or authenticate an address that requires no signatures.

### Proof of Concept
1. Construct a `MultisigSpendingCondition` with `hash_mode = MultisigHashMode::P2SH`, `signatures_required = 0`, and `fields = vec![]`.
2. Compute `signer = to_bits_p2sh(0, &[])` (the deterministic empty-multisig hash) offline using `public_keys_to_address_hash`, and set it as `self.signer`.
3. Build any `StacksTransaction` whose origin/sponsor auth uses this spending condition, with no signature fields populated.
4. Call `.verify()`: `num_sigs` (0) equals `signatures_required` (0), `StacksAddress::from_public_keys(0, &SerializeP2SH, 0, &[])` succeeds and returns the same precomputed hash, and `addr.bytes() == &self.signer` — `verify()` returns `Ok`, authenticating a transaction with zero signatures.
5. Any account whose spending-condition hash matches this fixed value (i.e., an account funded to that deterministic address) can now have its assets spent by anyone submitting such a transaction.

### Citations

**File:** stacks-codec/src/transaction.rs (L804-823)
```rust
    fn consensus_deserialize<R: Read>(
        fd: &mut R,
    ) -> Result<MultisigSpendingCondition, codec_error> {
        let hash_mode_u8: u8 = read_next(fd)?;
        let hash_mode = MultisigHashMode::from_u8(hash_mode_u8).ok_or(
            codec_error::DeserializeError(format!(
                "Failed to parse multisig spending condition: unknown hash mode {}",
                hash_mode_u8
            )),
        )?;

        let signer: Hash160 = read_next(fd)?;
        let nonce: u64 = read_next(fd)?;
        let tx_fee: u64 = read_next(fd)?;
        let fields: Vec<TransactionAuthField> = {
            let mut bound_read = BoundReader::from_reader(fd, MAX_MESSAGE_LEN as u64);
            read_next(&mut bound_read)
        }?;

        let signatures_required: u16 = read_next(fd)?;
```

**File:** stacks-codec/src/transaction.rs (L951-955)
```rust
        if num_sigs != self.signatures_required {
            return Err(AuthError::VerifyingError(
                "Incorrect number of signatures".to_string(),
            ));
        }
```

**File:** stacks-codec/src/transaction.rs (L963-971)
```rust
        let addr = StacksAddress::from_public_keys(
            0,
            &self.hash_mode.to_address_hash_mode(),
            self.signatures_required as usize,
            &pubkeys,
        )
        .ok_or_else(|| {
            AuthError::VerifyingError("Failed to generate address from public keys".to_string())
        })?;
```

**File:** stacks-codec/src/transaction.rs (L973-979)
```rust
        if addr.bytes() != &self.signer {
            return Err(AuthError::VerifyingError(format!(
                "Signer hash does not equal hash of public key(s): {} != {}",
                addr.bytes(),
                self.signer
            )));
        }
```

**File:** stacks-codec/src/transaction.rs (L996-1015)
```rust
    fn consensus_deserialize<R: Read>(
        fd: &mut R,
    ) -> Result<OrderIndependentMultisigSpendingCondition, codec_error> {
        let hash_mode_u8: u8 = read_next(fd)?;
        let hash_mode = OrderIndependentMultisigHashMode::from_u8(hash_mode_u8).ok_or(
            codec_error::DeserializeError(format!(
                "Failed to parse multisig spending condition: unknown hash mode {}",
                hash_mode_u8
            )),
        )?;

        let signer: Hash160 = read_next(fd)?;
        let nonce: u64 = read_next(fd)?;
        let tx_fee: u64 = read_next(fd)?;
        let fields: Vec<TransactionAuthField> = {
            let mut bound_read = BoundReader::from_reader(fd, MAX_MESSAGE_LEN as u64);
            read_next(&mut bound_read)
        }?;

        let signatures_required: u16 = read_next(fd)?;
```

**File:** stacks-codec/src/transaction.rs (L1139-1144)
```rust
        if num_sigs < self.signatures_required {
            return Err(AuthError::VerifyingError(format!(
                "Not enough signatures. Got {num_sigs}, expected at least {req}",
                req = self.signatures_required
            )));
        }
```

**File:** stacks-codec/src/transaction.rs (L1152-1160)
```rust
        let addr = StacksAddress::from_public_keys(
            0,
            &self.hash_mode.to_address_hash_mode(),
            self.signatures_required as usize,
            &pubkeys,
        )
        .ok_or_else(|| {
            AuthError::VerifyingError("Failed to generate address from public keys".to_string())
        })?;
```

**File:** stacks-codec/src/transaction.rs (L1162-1168)
```rust
        if addr.bytes() != &self.signer {
            return Err(AuthError::VerifyingError(format!(
                "Signer hash does not equal hash of public key(s): {} != {}",
                addr.bytes(),
                self.signer
            )));
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

**File:** stacks-common/src/address/mod.rs (L157-216)
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

/// Internally, the Stacks blockchain encodes address the same as Bitcoin
/// single-sig address over p2sh (p2h-p2wpkh)
fn to_bits_p2sh_p2wpkh<K: PublicKey>(pubk: &K) -> Hash160 {
    let key_hash = Hash160::from_data(&pubk.to_bytes());

    let bldr = Builder::new().push_int(0).push_slice(key_hash.as_bytes());

    let script = bldr.into_script();
    Hash160::from_data(script.as_bytes())
}

/// Internally, the Stacks blockchain encodes address the same as Bitcoin
/// multisig address over p2sh (p2sh-p2wsh)
fn to_bits_p2sh_p2wsh<K: PublicKey>(num_sigs: usize, pubkeys: &Vec<K>) -> Hash160 {
    let mut bldr = Builder::new();
    bldr = bldr.push_int(num_sigs as i64);
    for pubk in pubkeys {
        bldr = bldr.push_slice(&pubk.to_bytes());
    }
    bldr = bldr.push_int(pubkeys.len() as i64);
    bldr = bldr.push_opcode(btc_opcodes::OP_CHECKMULTISIG);

    let mut digest = Sha256::new();
    let mut d = [0u8; 32];

    digest.update(bldr.into_script().as_bytes());
    d.copy_from_slice(&digest.finalize());

    let ws = Builder::new().push_int(0).push_slice(&d).into_script();
    Hash160::from_data(ws.as_bytes())
}

/// Convert a number of required signatures and a list of public keys into a byte-vec to hash to an
/// address.  Validity of the hash_flag vis a vis the num_sigs and pubkeys will _NOT_ be checked.
/// This is a low-level method.  Consider using StacksAdress::from_public_keys() if you can.
pub fn public_keys_to_address_hash<K: PublicKey>(
    hash_flag: &AddressHashMode,
    num_sigs: usize,
    pubkeys: &Vec<K>,
) -> Hash160 {
    match *hash_flag {
        AddressHashMode::SerializeP2PKH => to_bits_p2pkh(&pubkeys[0]),
        AddressHashMode::SerializeP2SH => to_bits_p2sh(num_sigs, pubkeys),
        AddressHashMode::SerializeP2WPKH => to_bits_p2sh_p2wpkh(&pubkeys[0]),
        AddressHashMode::SerializeP2WSH => to_bits_p2sh_p2wsh(num_sigs, pubkeys),
    }
}
```
