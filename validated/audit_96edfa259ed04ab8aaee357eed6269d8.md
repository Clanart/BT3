### Title
Offset keys with odd-Y group keys produce invalid BIP-340 signatures — received funds become unspendable — ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
The USSD bug class — a valuation formula that is correct for `token0` but wrong when the token sits in the `token1` position — maps onto Serai's Bitcoin signer as a parity-dependent signing formula. The BIP-340 `Hram`/`verify` implementation correctly handles the odd/even parity of the nonce point `R` (it negates the challenge and the final `s`), but it assumes the group key `A` always represents an even-Y point, i.e. that the secret scalar `x` satisfies `x·G = A` with `A` even. `tweak_keys` guarantees this for the base key, but `SignableTransaction::multisig` re-derives per-input keys via `keys.clone().offset(self.offsets[i])` with no parity correction, so inputs whose offset key is odd-Y (~50% of registered offsets) can never produce a valid signature.

### Finding Description
In `crypto.rs`, `Hram::hram` hashes `x(A)` (correct, BIP-340 uses the x-only key) and negates the challenge `c` when `R` is odd, so shares compute `r - c·λ·x`. `Schnorr::verify` then sets `s = -sum` when `sig.R` is odd, yielding `s·G = R' + c·A` where `R'` is the even form of `R` — valid only when `A` itself is the even representative. [1](#0-0) [2](#0-1) 

BIP-340 requires signing with `-x` whenever `A` is odd, since the on-chain key is `x(A) = x(-A)`. Nothing performs that negation for offset keys:

- `tweak_keys` evenizes only the *base* tweaked key by scaling all shares by `-1` when the group key is odd. [3](#0-2) 
- `multisig` applies `keys.clone().offset(self.offsets[i])` to build each input's key and checks only `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`, which is x-only and therefore parity-blind. [4](#0-3) 
- `sign` then feeds each per-input sighash to the machine using the un-evenized offset key. [5](#0-4) 

When `offset.group_key()` is odd, `A_even = -A`, so a valid signature needs `s·G = R' - c·A`. The code produces `±(R - c·A)` (R even → `R + cA`; R odd → `-(R - cA) = -R + cA`), which never equals `R' - cA`. The formula is right for the even case and wrong for the odd case — the same "one ordering branch is wrong" shape as the USSD token0/token1 report.

### Impact Explanation
Any output received at an offset scalar yielding an odd-Y offset key produces inputs the threshold can sign "successfully" (or fail at `complete`), yet whose witness is not a valid BIP-340 signature for the prevout's x-only key. The transaction is rejected by the Bitcoin network, so funds reported as received are not spendable — for UTXOs at odd-parity offsets this is a permanent lock absent another key-negating recovery path. This affects roughly half of offset-derived receiving keys.

### Likelihood Explanation
An unprivileged party triggers this simply by sending Bitcoin to the multisig; the scanner registers offsets and `ReceivedOutput` values feed `SignableTransaction::new`/`multisig`. Offset scalars are hash-derived, so each registered offset yields an odd group key with probability ~1/2. No collusion, leaked keys, or malicious validator is required — only ordinary deposits to offset addresses.

### Recommendation
In `SignableTransaction::multisig` (or inside `ThresholdKeys::offset` usage for Bitcoin), after applying the per-input offset, check `needs_negation(&offset.group_key())` and conditionally `.scale(&-Scalar::ONE)` — the same pattern already used in `tweak_keys` — so every signing key's group key is even-Y before `AlgorithmMachine::new` consumes it. Alternatively, make the `Schnorr` algorithm itself parity-aware by negating the effective secret share when `A` is odd.

### Proof of Concept
1. Register an offset `o` such that `group_key = base + o·G` is odd-Y (retry offsets until odd; ~2 tries on average). Construct a `ReceivedOutput` paying to `p2tr_script_buf(group_key)`.
2. Build `SignableTransaction::new(vec![output], payments, change, None, fee)` and call `multisig(&keys)`; the x-only script check passes.
3. Run `preprocess`/`sign`/`complete` with the honest threshold. The emitted 64-byte witness `s` satisfies `s·G = R' + c·A` but the consensus check requires `s·G = R' + c·(-A)`; `Verify`/`OP_CHECKSIG` fails and the transaction cannot be broadcast.

Caveat: I could not read `ThresholdKeys::offset`'s implementation to fully rule out internal parity handling (my grep across `crypto/frost` returned no `fn offset`), but the x-only script check in `multisig` and the absence of any negation between `offset` and `AlgorithmMachine::new` in `send.rs` indicate none exists. If `offset` does silently negate, this finding collapses to a non-issue.

### Citations

**File:** networks/bitcoin/src/crypto.rs (L69-73)
```rust
      let c = Scalar::reduce(U256::from_be_slice(Sha256::from_engine(data).as_ref()));
      // If the nonce was odd, sign `r - cx` instead of `r + cx`, allowing us to negate `s` at the
      // end to sign as `-r + cx`
      <_>::conditional_select(&c, &-c, needs_negation(R))
    }
```

**File:** networks/bitcoin/src/crypto.rs (L145-149)
```rust
      self.0.verify(group_key, nonces, sum).map(|mut sig| {
        sig.s = <_>::conditional_select(&sum, &-sum, needs_negation(&sig.R));
        // Convert to a Bitcoin signature by dropping the byte for the point's sign bit
        sig.serialize()[1 ..].try_into().unwrap()
      })
```

**File:** networks/bitcoin/src/wallet/mod.rs (L67-74)
```rust
  let needs_negation = needs_negation(&keys.group_key());
  keys
    .scale(<_ as subtle::ConditionallySelectable>::conditional_select(
      &Scalar::ONE,
      &-Scalar::ONE,
      needs_negation,
    ))
    .expect("scaling keys by 1 or -1 yet interpreted as 0?")
```

**File:** networks/bitcoin/src/wallet/send.rs (L275-282)
```rust
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L381-391)
```rust
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
```
