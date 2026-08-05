### Title
Precompile signature verification (ed25519/secp256k1/secp256r1) binds only to attacker-chosen message bytes, with no domain separation to the invoking program, transaction, or consumer — enabling cross-context signature replay - (File: `precompiles/src/ed25519.rs`, `precompiles/src/secp256r1.rs`, `precompiles/src/secp256k1.rs`)

### Summary
The Stackup report describes `KeystoreAccount.isValidSignature` verifying a raw `hash` that carries no binding to the verifying contract's address, so a signature legitimately produced for one account can be replayed against another account sharing the same signer. Agave's built-in precompile programs (`ed25519_program`, `secp256k1_program`, `secp256r1_program`) exhibit the identical broken invariant at the validator level: `verify()` in each precompile checks only that `signature` over `message` is valid for `pubkey`, where `message` is an arbitrary byte slice pulled from instruction data at attacker-supplied offsets [1](#0-0) . Nothing in the verified value ties the signature to the invoking program ID, the specific transaction, a nonce, or any other domain separator — that binding is left entirely to whichever downstream program consumes the precompile's success signal.

### Finding Description
All three precompiles share the same verification shape: they locate `signature`, `pubkey`, and `message` slices (each of which may point into *any* instruction in the transaction via `signature_instruction_index` / `message_instruction_index`), and then simply verify signature-over-message-with-pubkey: [2](#0-1) [3](#0-2) 

The `verify()` function's output (success/failure of the precompile instruction) is the only thing a downstream program can rely on when it inspects the transaction via instruction-introspection to confirm "this pubkey signed this message." Crucially:
- The `message` bytes are entirely attacker/constructor chosen (`get_data_slice` just returns a byte range) [4](#0-3) .
- There is no mandatory inclusion of the calling program's ID, the transaction's recent blockhash, or a nonce in what gets signed — the precompile has no opinion about it.
- The precompile list itself has no notion of "which program requested this check", so the identical `(message, signature, pubkey)` triple satisfies the precompile for *any* program that happens to check for it in the same transaction.

This is structurally identical to the reported ERC-1271 bug: the verifier confirms "this key signed this exact bytes", but the exact bytes do not identify *which contract/program the user believed they were authorizing*. If an off-chain signer (e.g., a guardian/oracle/multisig key) is shared across two different on-chain consumers that both accept messages of the same shape (e.g., both expect `sha256(action || amount)` with no program-id or account-address component), a signature produced for consumer A's instruction can be copied verbatim into a transaction invoking consumer B, and the ed25519/secp256r1/secp256k1 precompile will validate it identically, because Agave's built-in verifier makes no distinction between the two contexts.

### Impact Explanation
This is a real, security-relevant caveat of Agave's precompile design that has caused actual fund-theft incidents on Solana when application authors did not defensively add program-id/account/nonce binding themselves (this is precisely why Solana's own docs and multiple audits flag "the ed25519/secp256k1 program instruction is not bound to the invoking program" as a foot-gun). Because Agave provides no protocol-level enforcement of domain separation, any built-in guarantee a program author might assume — "if I see a passing ed25519 precompile instruction for pubkey P and my expected message, then P endorsed *my* program's specific action" — is false. This can lead to fund theft/loss or false-execution-acceptance in any program that shares a signer key across multiple consumers or across different actions without itself embedding a domain tag (program ID / account address / nonce) in the signed payload.

### Likelihood Explanation
Likelihood is contingent on the downstream program's message construction, not on any private/trusted assumption — an unprivileged party (any transaction submitter) can simply copy an already-broadcast, publicly visible `(pubkey, message, signature)` from one transaction and paste it into a new instruction addressed to a different consuming program. No validator, peer, or leader collusion is required, and no leaked key is needed — this is pure signature replay using the public contents of a valid instruction. The precondition (message not including a domain separator) is exactly the same precondition that made the original report a "Medium" finding; it is a design characteristic of the three built-in precompiles, applicable everywhere they're used.

### Recommendation
Since the precompiles themselves are generic verifiers by design, mitigation must be documented/enforced at the interface level:
- Document clearly (and, where possible, provide a canonical helper/library) that any program relying on `ed25519_program`/`secp256k1_program`/`secp256r1_program` instruction introspection MUST include a domain separator in the signed message: at minimum the consuming program's ID, plus a per-use nonce or the target account's pubkey, analogous to ERC-7739's defensive rehashing.
- Consider adding an optional "program_id binding" convention/instruction extension so a signed message can be canonically tied to the invoking program without relying purely on programmer discipline.

### Proof of Concept
1. Program A and Program B both use an ed25519 guardian key `G` and both expect a signed message of the form `sha256(action_type || amount)` with no program ID or account pubkey embedded.
2. A user obtains a valid `(message="WITHDRAW:1000", signature, G)` triple from a transaction that called Program A (e.g., by observing it on-chain).
3. The user submits a new transaction containing an `ed25519_program` instruction with the identical `message`/`signature`/`pubkey`, followed by a CPI to Program B, which performs its own instruction introspection to find the matching ed25519 precompile instruction: [5](#0-4) 
4. `agave_precompiles::ed25519::verify` succeeds because it only checks the raw bytes/signature/pubkey relationship, with no way to know or enforce that the signer only ever intended this message for Program A [6](#0-5) .
5. Program B treats the signature as valid authorization for its own action, resulting in unauthorized state change/fund movement.

**Caveat/uncertainty:** This finding describes a documented Agave/Solana design characteristic of the built-in precompiles (not a newly introduced code defect), and its concrete exploitability depends on downstream program message-construction choices, which are outside Agave's own code. I could not find Agave-internal code that itself misuses the precompiles without a nonce (the built-in `ed25519-tests`/`secp256r1` test/benchmark helpers all sign fixed strings like `b"hello"` purely for testing) [7](#0-6) , so I cannot point to a first-party Agave consumer that is itself vulnerable — the exposure is inherent to the verifier contract that any third-party program built on top of these built-ins inherits.

### Citations

**File:** precompiles/src/ed25519.rs (L11-29)
```rust
pub fn verify(
    data: &[u8],
    instruction_datas: &[&[u8]],
    _feature_set: &FeatureSet,
) -> Result<(), PrecompileError> {
    if data.len() < SIGNATURE_OFFSETS_START {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let num_signatures = data[0] as usize;
    if num_signatures == 0 && data.len() > SIGNATURE_OFFSETS_START {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let expected_data_size = num_signatures
        .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
        .saturating_add(SIGNATURE_OFFSETS_START);
    // We do not check or use the byte at data[1]
    if data.len() < expected_data_size {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
```

**File:** precompiles/src/ed25519.rs (L42-76)
```rust
        // Parse out signature
        let signature = get_data_slice(
            data,
            instruction_datas,
            offsets.signature_instruction_index,
            offsets.signature_offset,
            SIGNATURE_SERIALIZED_SIZE,
        )?;

        let signature =
            Signature::from_bytes(signature).map_err(|_| PrecompileError::InvalidSignature)?;

        // Parse out pubkey
        let pubkey = get_data_slice(
            data,
            instruction_datas,
            offsets.public_key_instruction_index,
            offsets.public_key_offset,
            PUBKEY_SERIALIZED_SIZE,
        )?;

        let publickey = ed25519_dalek::PublicKey::from_bytes(pubkey)
            .map_err(|_| PrecompileError::InvalidPublicKey)?;

        // Parse out message
        let message = get_data_slice(
            data,
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;
        publickey
            .verify_strict(message, &signature)
            .map_err(|_| PrecompileError::InvalidSignature)?;
```

**File:** precompiles/src/ed25519.rs (L81-105)
```rust
fn get_data_slice<'a>(
    data: &'a [u8],
    instruction_datas: &'a [&[u8]],
    instruction_index: u16,
    offset_start: u16,
    size: usize,
) -> Result<&'a [u8], PrecompileError> {
    let instruction = if instruction_index == u16::MAX {
        data
    } else {
        let signature_index = instruction_index as usize;
        if signature_index >= instruction_datas.len() {
            return Err(PrecompileError::InvalidDataOffsets);
        }
        instruction_datas[signature_index]
    };

    let start = offset_start as usize;
    let end = start.saturating_add(size);
    if end > instruction.len() {
        return Err(PrecompileError::InvalidDataOffsets);
    }

    Ok(&instruction[start..end])
}
```

**File:** precompiles/src/secp256r1.rs (L89-140)
```rust
        // Parse out message
        let message = get_data_slice(
            data,
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;

        let r_bignum = BigNum::from_slice(&signature[..FIELD_SIZE])
            .map_err(|_| PrecompileError::InvalidSignature)?;
        let s_bignum = BigNum::from_slice(&signature[FIELD_SIZE..])
            .map_err(|_| PrecompileError::InvalidSignature)?;

        // Check that the signature is generally in range
        let within_range = r_bignum >= one
            && r_bignum <= order_minus_one
            && s_bignum >= one
            && s_bignum <= half_order;

        if !within_range {
            return Err(PrecompileError::InvalidSignature);
        }

        // Create an ECDSA signature object from the ASN.1 integers
        let ecdsa_sig = openssl::ecdsa::EcdsaSig::from_private_components(r_bignum, s_bignum)
            .and_then(|sig| sig.to_der())
            .map_err(|_| PrecompileError::InvalidSignature)?;

        let public_key_point = EcPoint::from_bytes(&group, pubkey, &mut ctx)
            .map_err(|_| PrecompileError::InvalidPublicKey)?;
        let public_key = EcKey::from_public_key(&group, &public_key_point)
            .map_err(|_| PrecompileError::InvalidPublicKey)?;
        let public_key_as_pkey =
            PKey::from_ec_key(public_key).map_err(|_| PrecompileError::InvalidPublicKey)?;

        let mut verifier =
            Verifier::new(openssl::hash::MessageDigest::sha256(), &public_key_as_pkey)
                .map_err(|_| PrecompileError::InvalidSignature)?;
        verifier
            .update(message)
            .map_err(|_| PrecompileError::InvalidSignature)?;

        if !verifier
            .verify(&ecdsa_sig)
            .map_err(|_| PrecompileError::InvalidSignature)?
        {
            return Err(PrecompileError::InvalidSignature);
        }
    }
    Ok(())
}
```

**File:** programs/ed25519-tests/tests/process_transaction.rs (L16-21)
```rust
    let message_arr = b"hello";
    let keypair = Keypair::new();
    let signature = keypair.sign_message(message_arr);
    let pubkey = keypair.pubkey().to_bytes();
    let instruction =
        new_ed25519_instruction_with_signature(message_arr, signature.as_array(), &pubkey);
```
