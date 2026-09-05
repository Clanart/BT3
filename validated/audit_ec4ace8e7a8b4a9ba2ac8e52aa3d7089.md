### Title
0-of-0 multisig spending condition authenticates a transaction with zero signatures - ([File: stacks-codec/src/transaction.rs])

### Summary
The Basket report's root cause is that `validateWeights` only checks that two arrays have equal length, never that the length is non-zero, so an empty/degenerate configuration is accepted as valid and later trusted as if it enforced a real economic constraint. The same "equality-without-a-floor" pattern exists in `MultisigSpendingCondition::verify` / `OrderIndependentMultisigSpendingCondition::verify`: the code only ever checks `num_sigs == self.signatures_required` (or `>=` for order-independent), and never rejects `signatures_required == 0`. Combined with `StacksAddress::from_public_keys`, which only requires `pubkeys.len() >= num_sigs` (trivially true when `num_sigs == 0`), a spending condition with `signatures_required = 0` and an empty `fields` vector authenticates successfully without a single signature.

### Finding Description
`MultisigSpendingCondition::verify` [1](#0-0)  loops over `self.fields`; if `fields` is empty, `num_sigs` stays `0`. It then checks:
```
if num_sigs != self.signatures_required { ... }
```
which passes trivially when `signatures_required == 0`. There is no check anywhere that `signatures_required > 0` (nor that `fields` is non-empty), mirroring the Basket bug's missing "length > 0" check.

The code then derives the expected signer address purely from `signatures_required` and the (empty) `pubkeys` list via `StacksAddress::from_public_keys` [2](#0-1) , which only rejects when `pubkeys.len() < num_sigs` — never true for `num_sigs = 0`. For hash mode `SerializeP2SH`, this calls `to_bits_p2sh(0, &vec![])` [3](#0-2) , which builds the fixed Bitcoin-style script `OP_0 OP_0 OP_CHECKMULTISIG` (no key data at all, since `pubkeys` is empty) and returns its `Hash160`. This hash is a **fixed, deterministic value** — identical for every attacker, requiring no keys.

Consequently, anyone can construct a `TransactionSpendingCondition::Multisig` with `hash_mode = P2SH`, `fields = vec![]`, `signatures_required = 0`, and `signer` set to this fixed, publicly-computable Hash160. `verify()` returns `Ok(...)` with **zero signatures checked**, exactly as `validateWeights` in the Basket contract accepted a proposal with zero tokens/weights and let downstream logic (`pullUnderlying`) skip the payment check entirely because `weights.length == 0`.

The `OrderIndependentMultisigSpendingCondition::verify` path has the same gap (`num_sigs < self.signatures_required` is vacuously satisfied for `signatures_required = 0`) [4](#0-3) .

### Impact Explanation
This breaks the fundamental equality the report's rules require: "signatures verified fewer than the threshold." Here the threshold itself can be attacker-chosen as zero, so the number of valid signatures required to authorize spending from a specific, deterministic address is zero. Any STX or asset that ends up associated with that fixed "0-of-0" address (e.g., via an accidental transfer, a buggy wallet/tool that derives such an address, or any protocol path that computes an address from `signatures_required`/`pubkeys` without enforcing `signatures_required >= 1`) can be spent by anyone, with no valid signature at all — a strict equality break of the signature-threshold invariant, matching the High/Critical impact class ("an asset moving without authorization").

### Likelihood Explanation
The address is fully deterministic and requires no private key knowledge or race condition — merely knowledge of the codec (public knowledge, since it's in this open-source repo). However, real-world exploitation is gated entirely on someone else's funds being associated with that exact fixed hash, which is a low-probability, out-of-attacker-control event; I could not find (within the indexed portion of the codebase) any code path that enforces `signatures_required >= 1` at construction, deserialization, or mempool-admission time elsewhere in `stackslib/src/chainstate/stacks/auth.rs` or `stackslib/src/chainstate/stacks/transaction.rs`, but I also could not confirm there is no such guard given index size limits — a full audit of every call site of `MultisigSpendingCondition`/`OrderIndependentMultisigSpendingCondition` construction and validation would require access to the complete file contents, which the current index may not provide in full.

### Recommendation
Reject `signatures_required == 0` (and correspondingly reject `num_sigs == 0`-based address derivation) in `MultisigSpendingCondition::verify`, `OrderIndependentMultisigSpendingCondition::verify`, and in `StacksAddress::from_public_keys` when `hash_mode` is `SerializeP2SH`/`SerializeP2WSH`, mirroring the recommended Basket fix of requiring `_tokens.length > 0`. Concretely, add `if self.signatures_required == 0 { return Err(...) }` at the top of both `verify` functions, and add `if num_sigs == 0 { return None; }` in `from_public_keys` for multisig hash modes.

### Proof of Concept
1. Compute `addr_hash = Hash160(Script(OP_0, OP_0, OP_CHECKMULTISIG))` — this is a fixed value, reproducible via `to_bits_p2sh(0, &vec![])` with an empty pubkey vector [3](#0-2) .
2. Construct a transaction whose origin `TransactionAuth` is `TransactionSpendingCondition::Multisig(MultisigSpendingCondition { signer: addr_hash, hash_mode: MultisigHashMode::P2SH, nonce, tx_fee, fields: vec![], signatures_required: 0 })`.
3. Submit the transaction; `MultisigSpendingCondition::verify` computes `num_sigs = 0`, passes `num_sigs != signatures_required` (0 == 0), derives `addr = from_public_keys(0, P2SH, 0, &[])` = the same fixed hash, matches `self.signer`, and returns `Ok(cur_sighash)` — the transaction is authenticated with zero signatures [5](#0-4) .
4. Any balance associated with the address derived from `addr_hash` can be spent by an attacker with no key material whatsoever.

### Citations

**File:** stacks-codec/src/transaction.rs (L909-981)
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
```

**File:** stacks-codec/src/transaction.rs (L1099-1170)
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
