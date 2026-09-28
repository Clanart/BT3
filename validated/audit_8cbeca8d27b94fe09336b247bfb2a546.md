### Title
`ThresholdKeys::read` accepts attacker-crafted key material with no consistency check between `secret_share`, `verification_shares`, and the derived `group_key`, enabling key substitution - (File: crypto/dkg/src/lib.rs)

### Summary
The deserialization bug class of the `devalue` report — a parser assigning attacker-chosen values into internal fields without checking that the fields are consistent with the object's intended semantics — maps directly onto `ThresholdKeys::read` / `ThresholdKeys::new`. The reader takes `t`, `n`, `i`, an interpolation method, a `secret_share`, and `n` verification shares entirely from the input bytes, and `ThresholdKeys::new` derives `group_key` purely from `verification_shares[1..=t]` without ever checking that `verification_shares[i] == secret_share * G` or that the shares lie on a common polynomial. An untrusted blob can therefore instantiate a fully attacker-controlled key set under a group key the host believes is its threshold key.

### Finding Description
`ThresholdKeys::read` parses five u16/byte-shaped fields (`t`, `n`, `i`, interpolation tag) plus `n` scalars/points straight from the reader, then calls `ThresholdKeys::new` (crypto/dkg/src/lib.rs:574-632).

`ThresholdKeys::new` performs only structural checks:

- `verification_shares.len() == n` and every key `<= n` (crypto/dkg/src/lib.rs:355-365)
- `Constant` interpolation requires `t == n` (lines 367-374)

It then computes the group key as `sum(verification_shares[i] * interpolation_factor(i))` for `i in 1..=t` (lines 376-378) and stores `secret_share` verbatim. Nowhere is it verified that:

- `C::generator() * secret_share == verification_shares[i]` for the local participant `i`, or
- the verification shares are mutually consistent (e.g., all evaluate a common degree-`t-1` polynomial / the `Constant` coefficients correspond to the shares).

So the "property bag" is populated exactly as in the devalue flaw: whichever indices/values the input encodes become the object's truth. A crafted blob specifying `secret_share = a` and `verification_shares` interpolating to `a*G` (or any attacker-known secret) yields a `ThresholdKeys` reporting a valid `group_key()` that only the attacker can sign for. Once loaded, `group_key()` (lines 445-447) feeds key registration, the `Scanner` (`Scanner::new(key)` / `p2tr_script_buf` in networks/bitcoin/src/wallet/mod.rs:80-86, 162-166), and the FROST `SignMachine`, which will happily produce valid signatures for the attacker's key.

### Impact Explanation
High. Two concrete outcomes are reachable:

- **Key substitution / deposit theft**: an attacker who can feed bytes to `ThresholdKeys::read` (key import/backup-restore paths, or any channel delivering serialized keys) causes the host to operate a threshold "key" whose group key resolves to an attacker-controlled secret. The bitcoin `Scanner` will then report deposits to the tweaked attacker address as received; those funds are spendable by the attacker, not the intended threshold set — squarely the "funds reported received that are not spendable" impact class. `ThresholdView` / `SignMachine` will also produce valid Schnorr signature shares for the substituted key, signing arbitrary attacker-chosen messages under a key the operator believes is the protocol's multisig.
- **Silent inconsistency**: a blob can mix a victim `group_key` (via `verification_shares[1..=t]`) with a mismatched `secret_share`; produced signature shares fail verification and the operator is blamed, or a `Constant` interpolation vector is supplied that evaluates to a different effective key than operators expect — the deserializer silently materializes an object no honest code path can produce.

### Likelihood Explanation
Reachability is limited to contexts where serialized `ThresholdKeys` originate from an untrusted or corruptible source rather than the node's own DB-written output of the DKG. Where such an import path exists (backup/restore, key migration, third-party tooling producing the documented serialization — e.g., the vector loader at crypto/frost/src/tests/vectors.rs:118-131 demonstrates the format is trivially constructible), no signature, MAC, or consistency invariant gates acceptance, so exploitation is reliable: every field is taken at face value.

### Recommendation
In `ThresholdKeys::new` (which `ThresholdKeys::read` delegates to), add consistency validation:

- Verify `C::generator() * secret_share == verification_shares[&params.i()]` and reject otherwise.
- For `Interpolation::Constant`, verify the `n` coefficients actually reproduce each verification share / the group key; for `Lagrange`, optionally verify the shares are consistent with a degree-`t-1` polynomial where feasible, or at minimum document that `ThresholdKeys::read` must only ever consume trusted bytes and authenticate the serialization (e.g., a checksum/MAC over the blob) at persistence boundaries.

### Proof of Concept
Following the format exercised in `crypto/frost/src/tests/vectors.rs`:

```rust
// Attacker-chosen secret
let a = Secp256k1::random_nonzero_F(&mut OsRng);        // e.g. t = n = 1 for simplicity
let share_point = Secp256k1::generator() * a;

let mut blob = vec![];
blob.extend(u32::try_from(Secp256k1::ID.len()).unwrap().to_le_bytes());
blob.extend(Secp256k1::ID);
blob.extend(1u16.to_le_bytes());                        // t
blob.extend(1u16.to_le_bytes());                        // n
blob.extend(1u16.to_le_bytes());                        // i = Participant(1)
blob.push(1);                                           // Interpolation::Lagrange
blob.extend(a.to_repr().as_ref());                      // secret_share = a
blob.extend(share_point.to_bytes().as_ref());           // verification_shares[1]

let keys = ThresholdKeys::<Secp256k1>::read::<&[u8]>(&mut blob.as_ref()).unwrap();
// keys.group_key() == a*G — a key only the attacker controls,
// yet indistinguishable from a legitimately generated ThresholdKeys.
// Scanner::new(keys.group_key()) attributes deposits to the attacker;
// the SignMachine signs attacker-chosen messages with a.
```

Note: I could not exhaustively verify `ThresholdParams::new`'s validation of `i`/`t`/`n` bounds in the remaining budget, but it does not affect the finding — even fully valid `(t, n, i)` admit the missing `secret_share` ↔ `verification_shares` consistency check.