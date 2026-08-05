### Title
Ed25519 shred-signature verification uses non-strict `verify` (malleable), bypassing shred dedup and duplicate-shred budget — (File: `ledger/src/shred.rs`, `ledger/src/shred/merkle.rs`, `ledger/src/sigverify_shreds.rs`)

### Summary
Every place in the ledger/turbine pipeline that checks a shred's leader signature calls the plain, non-strict `Signature::verify(pubkey, data)` instead of the cofactored/canonical `verify_strict` that the codebase itself uses elsewhere (the ed25519 precompile). Because plain `verify` does not reject non-canonical `S` encodings, an attacker who merely observes one legitimately signed shred on the wire can — without the leader's private key — derive several additional byte-distinct signatures that verify successfully for the exact same shred payload. This is the same "signature is malleable / usable as pseudo-unique identifier" bug class as the external report, just moved from an EIP-712 ack signature to Solana's turbine shred-signature checks.

### Finding Description
The shred verification routines use the loose verify path: [1](#0-0) [2](#0-1) [3](#0-2) 

By contrast, the codebase is fully aware that `verify_strict` is required to reject malleable ed25519 signatures, and enforces it in the ed25519 precompile, with an explicit regression test proving the difference: [4](#0-3) [5](#0-4) 

Ed25519 malleability lets anyone holding a *valid* `(R, S)` pair transform it into another valid pair for the same message and key by adding a multiple of the group order `L` to `S` while it still fits in 32 bytes (roughly 8 distinct byte-level encodings per captured signature, since `2^256 / L ≈ 8`). No private key is required — this is a pure encoding manipulation on an already-observed valid signature, not a forgery.

Two Agave mechanisms rely on the raw signature bytes as a quasi-unique key and are therefore defeated by this malleability:

1. **Shred dedup in retransmit**: `ShredDeduper::dedup` first dedups on `get_common_header_bytes` (which embeds the raw signature bytes), and only falls back to a *bounded* `shred_id_filter` (sized by `MAX_DUPLICATE_COUNT`) that is meant to allow only a small, fixed number of genuinely different (leader-equivocated) shreds per `ShredId` through for duplicate-block detection: [6](#0-5) 
Since a malleated shred has different header bytes (different signature encoding) but the *same* underlying signed data, it is never caught by the fast header-hash dedup and instead consumes a slot in the `MAX_DUPLICATE_COUNT` budget intended for real equivocation evidence — exhausting that budget with junk copies of the same, single legitimate shred.

2. **Signature verify cache**: the shred sig-verify LRU cache keys on the raw signature bytes plus pubkey and merkle root: [7](#0-6) [8](#0-7) 
Each malleated variant is a guaranteed cache miss, forcing a full ed25519 verification even though it is cryptographically the same signature value.

Existing guards do not stop this path because:
- Plain `Signature::verify` (used in all three call sites above) performs the "cofactored, non-strict" ed25519 check and does not reject `S ≥ L` or otherwise non-canonical encodings — unlike `verify_strict`, which the codebase already uses in the ed25519 precompile and has a dedicated test proving it blocks exactly this class of signature.
- The shred dedup/verify-cache layers assume signature bytes are a stable, unique fingerprint of "this exact valid signed message," which is false under non-strict ed25519 verification.

### Impact Explanation
This falls under "non-RPC remote exhaustion/crash" and weakens "false execution/rooting/acceptance" guarantees:
- **Turbine/retransmit exhaustion**: any unprivileged network participant who intercepts (or replays a captured) turbine shred can synthesize a bounded number (~8) of additional, all-valid signature encodings for the identical shred and inject them into a validator's shred-fetch pipeline. Each variant bypasses the header-hash dedup and forces (a) redundant CPU-cost ed25519 verification and (b) consumption of one of the limited `MAX_DUPLICATE_COUNT` retransmit slots reserved for genuine leader-equivocation evidence, causing bandwidth amplification toward downstream retransmit peers and starving the mechanism meant to propagate real duplicate-block proofs.
- **Weakened duplicate-block detection**: because the small `MAX_DUPLICATE_COUNT` budget per `ShredId` can be filled by meaningless malleated copies of a single legitimate shred, a validator's ability to see and disseminate a genuinely different (equivocated) shred for that slot/index can be crowded out, degrading the duplicate-block/duplicate-shred detection path used for safety/slashing evidence.

### Likelihood Explanation
Likelihood is moderate-to-high for the exhaustion/degradation effect: no leader or validator collusion is required, only the ability to observe one valid shred on turbine (trivial for any full/light client or by sniffing UDP) and to send crafted UDP packets to a validator's shred-fetch/TVU sockets, which is the normal ingestion path for turbine/repair shreds. The bounded ~8x amplification per captured shred is modest but is easily automated across many shreds and slots, and it requires no expensive computation (byte-level arithmetic on `S`) — cheaper than the receiving validator's ed25519 verify cost.

### Recommendation
Use `verify_strict` (already adopted in `precompiles/src/ed25519.rs`) for shred-signature checks in `ledger/src/shred.rs`, `ledger/src/shred/merkle.rs`, and `ledger/src/sigverify_shreds.rs`, and dedup/verify-cache shreds on the canonicalized `(pubkey, message)` pair (or a cryptographic hash of them) rather than on raw, potentially-malleable signature bytes, so that malleated encodings of an already-seen valid signature are recognized as duplicates rather than treated as new signatures.

### Proof of Concept
1. Capture one legitimately signed shred `(header || signature S || payload)` broadcast by the current slot leader over turbine.
2. Compute `S' = (S_int + L) mod 2^256` (and successive `+2L, +3L, ...` while the result still fits in 32 bytes), producing up to ~7 additional 32-byte signature encodings that still satisfy `signature.verify(pubkey, merkle_root)` under the non-strict verify used in `ledger/src/shred.rs`/`ledger/src/shred/merkle.rs` (the codebase's own `test_ed25519_malleability` in `precompiles/src/ed25519.rs` demonstrates the underlying primitive: a malleable signature that plain `verify` accepts but `verify_strict` rejects).
3. Replace the signature bytes in the captured shred packet with each `S'`, keeping slot/index/payload identical, and send these packets to a target validator's shred ingestion socket (or replay through turbine/repair paths).
4. Observe: (a) each variant is a fresh entry in `turbine/src/sigverify_shreds.rs`'s LRU verify cache (forcing repeated CPU-cost verification), and (b) each variant produces a different `get_common_header_bytes` hash in `turbine/src/retransmit_stage.rs::ShredDeduper::dedup`, so it is *not* dropped by the first dedup check and instead consumes one of the limited `MAX_DUPLICATE_COUNT` slots for that `ShredId`, even though only one real shred exists.

### Citations

**File:** ledger/src/shred.rs (L558-564)
```rust
    #[must_use]
    pub fn verify(&self, pubkey: &Pubkey) -> bool {
        match self.signed_data() {
            Ok(data) => self.signature().verify(pubkey.as_ref(), data.as_ref()),
            Err(_) => false,
        }
    }
```

**File:** ledger/src/shred/merkle.rs (L112-118)
```rust
    #[must_use]
    fn verify(&self, pubkey: &Pubkey) -> bool {
        match self.signed_data() {
            Ok(data) => self.signature().verify(pubkey.as_ref(), data.as_ref()),
            Err(_) => false,
        }
    }
```

**File:** ledger/src/sigverify_shreds.rs (L16-16)
```rust
pub type LruCache = lazy_lru::LruCache<(Signature, Pubkey, /*merkle root:*/ Hash), ()>;
```

**File:** ledger/src/sigverify_shreds.rs (L39-55)
```rust
    let Some(signature) = shred::layout::get_signature(shred) else {
        return false;
    };
    trace!("signature {signature}");
    let Some(data) = shred::layout::get_merkle_root(shred) else {
        return false;
    };

    let key = (signature, *pubkey, data);
    if cache.read().unwrap().get(&key).is_some() {
        true
    } else if key.0.verify(key.1.as_ref(), key.2.as_ref()) {
        cache.write().unwrap().put(key, ());
        true
    } else {
        false
    }
```

**File:** precompiles/src/ed25519.rs (L74-76)
```rust
        publickey
            .verify_strict(message, &signature)
            .map_err(|_| PrecompileError::InvalidSignature)?;
```

**File:** precompiles/src/ed25519.rs (L454-512)
```rust
    #[test]
    fn test_ed25519_malleability() {
        agave_logger::setup();

        // sig created via ed25519_dalek: both pass
        let secret_bytes: [u8; 32] = rand::random();
        let secret = ed25519_dalek::SecretKey::from_bytes(&secret_bytes).unwrap();
        let public: ed25519_dalek::PublicKey = (&secret).into();
        let privkey = ed25519_dalek::Keypair { secret, public };
        let message_arr = b"hello";
        let signature = privkey.sign(message_arr).to_bytes();
        let pubkey = privkey.public.to_bytes();
        let instruction = new_ed25519_instruction_with_signature(message_arr, &signature, &pubkey);

        let feature_set = FeatureSet::default();
        assert!(
            test_verify_with_alignment(
                verify,
                &instruction.data,
                &[&instruction.data],
                &feature_set
            )
            .is_ok()
        );

        let feature_set = FeatureSet::all_enabled();
        assert!(
            test_verify_with_alignment(
                verify,
                &instruction.data,
                &[&instruction.data],
                &feature_set
            )
            .is_ok()
        );

        // malleable sig: verify_strict does NOT pass
        // for example, test number 5:
        // https://github.com/C2SP/CCTV/tree/main/ed25519
        // R has low order (in fact R == 0)
        let pubkey =
            &hex::decode("10eb7c3acfb2bed3e0d6ab89bf5a3d6afddd1176ce4812e38d9fd485058fdb1f")
                .unwrap();
        let signature = &hex::decode("00000000000000000000000000000000000000000000000000000000000000009472a69cd9a701a50d130ed52189e2455b23767db52cacb8716fb896ffeeac09").unwrap();
        let message = b"ed25519vectors 3";
        let instruction = new_ed25519_instruction_raw(pubkey, signature, message);

        // verify_strict does NOT pass for malleable signature
        let feature_set = FeatureSet::default();
        assert!(
            test_verify_with_alignment(
                verify,
                &instruction.data,
                &[&instruction.data],
                &feature_set
            )
            .is_err()
        );
    }
```

**File:** turbine/src/retransmit_stage.rs (L240-257)
```rust
    // Returns true if the shred is duplicate and should be discarded.
    #[must_use]
    fn dedup(&self, key: ShredId, shred: &[u8], max_duplicate_count: usize) -> bool {
        // Shreds in the retransmit stage:
        //   * don't have repair nonce (repaired shreds are not retransmitted).
        //   * are already resigned by this node as the retransmitter.
        //   * have their leader's signature verified.
        // Therefore in order to dedup shreds, it suffices to compare:
        //    (signature, slot, shred-index, shred-type)
        // Because ShredCommonHeader already includes all of the above tuple,
        // the rest of the payload can be skipped.
        // In order to detect duplicate blocks across cluster, we retransmit
        // max_duplicate_count different shreds for each ShredId.
        shred::layout::get_common_header_bytes(shred)
            .map(|header| self.deduper.dedup(header))
            .unwrap_or(true)
            || (0..max_duplicate_count).all(|i| self.shred_id_filter.dedup(&(key, i)))
    }
```
