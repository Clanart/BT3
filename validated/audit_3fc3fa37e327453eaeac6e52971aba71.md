### Title
Unverified `SumHint.final_pubkey` binding lets a caller make the wallet signer misuse an owned private key against an arbitrary "aggregated" public key - (File: chia/wallet/wallet_signer.py)

### Summary
`CVE-2021-3521` is a "missing binding-signature check" bug: RPM imports an OpenPGP subkey and trusts it as belonging to a primary key without verifying the cryptographic binding between them. The analogous pattern in this codebase is in `WalletSigner.execute_signing_instructions`, which accepts caller-supplied `SumHint` objects and trusts the `final_pubkey` field as the "parent" key that a set of "child" keys (fingerprints + synthetic offset) sum to, without ever checking that `final_pubkey` actually equals the sum of those constituent public keys [1](#0-0) .

### Finding Description
`SigningInstructions.key_hints.sum_hints` is a caller-controlled, `clvm_streamable` structure containing `fingerprints`, a `synthetic_offset`, and a `final_pubkey` [2](#0-1) .

In `execute_signing_instructions`, for each `SumHint` the code:
1. Looks up whether it already has private keys for the listed `fingerprints` (rejecting/skipping if not, depending on `partial_allowed`).
2. Derives an `offset_sk`/`offset_pk` purely from `sum_hint.synthetic_offset` (attacker-controlled bytes, no validation against a known derivation path).
3. Parses `sum_hint.final_pubkey` directly from bytes and registers it in `sum_hint_lookup[final_fingerprint] = [*fingerprints_we_have, offset_pk.get_fingerprint()]` — with **no check that `final_pubkey == sum(pubkeys for fingerprints_we_have) + offset_pk`** [1](#0-0) .

Later, when a `SigningTarget` names that `final_pubkey`'s fingerprint, the signer happily signs the target message using the real (owned) private key(s), but with the caller-supplied `final_pubkey` used as the BLS "aggregation info" prefix in `AugSchemeMPL.sign(sk, message, pk_lookup[pk_fingerprint])` [3](#0-2) . This is exactly the missing-binding-verification pattern from the CVE: a "subkey" (the claimed sum/aggregate key) is accepted and used for cryptographic operations without proving it is actually bound to (i.e., actually equals the sum of) the keys the signer holds.

### Impact Explanation
This breaks the invariant that a `SumHint` accurately describes which owned keys compose a given aggregate public key. Because the signer never validates the binding, a caller of the signer-protocol RPCs (`gather_signing_info` / `execute_signing_instructions`, exposed via `chia/wallet/wallet_rpc_api.py`) can supply a `final_pubkey` that does not correspond to the declared `fingerprints`/`synthetic_offset`, causing the wallet to produce BLS signature shares keyed (via the Aug scheme prefix) to an arbitrary attacker-chosen public key while consuming the wallet's real private keys. This corrupts the trust boundary the CHIP-0028 signer protocol relies on for offline/partial signing workflows (`partial_allowed=True` path), and any downstream component that assumes a validated `SumHint` before aggregating partial signatures inherits the flaw. It is a data-integrity issue in the wallet's own signing pipeline, matching the CVE's core theme (unvalidated key-binding trusted for cryptographic operations) rather than a proof-of-space/consensus bug.

### Likelihood Explanation
Reaching this code requires driving `SigningInstructions` into `WalletSigner.execute_signing_instructions`, which is reachable through the wallet's signer-protocol RPC endpoints (`gather_signing_info` / `execute_signing_instructions` in `chia/wallet/wallet_rpc_api.py`) available to any local RPC caller with wallet RPC access, and through the offline-signer / partial-multisig flow that explicitly sets `partial_allowed=True` to permit sum hints referencing keys not fully held locally [4](#0-3) . This is a normal, documented use path (multisig/offline signing), not a privileged or malicious-peer-only path, so likelihood is moderate.

### Recommendation
Before trusting `sum_hint.final_pubkey`, verify the binding explicitly: compute `expected_pubkey = sum(pk_lookup[f] for f in fingerprints_we_have) + offset_pk` and require `G1Element.from_bytes(sum_hint.final_pubkey) == expected_pubkey` (only registering the hint into `sum_hint_lookup` when the check passes, otherwise treating it like an unresolved fingerprint under the existing `partial_allowed` error/skip logic).

### Proof of Concept
Not exploitable via a simple external PoC without wallet RPC access; conceptually: an RPC caller with access to the wallet's `execute_signing_instructions` endpoint submits a `SigningInstructions` whose `KeyHints.sum_hints` contains a `SumHint` with `fingerprints=[<owned_fingerprint>]`, an attacker-chosen `synthetic_offset`, and a `final_pubkey` that is *not* the true sum of the owned key and the offset key. A matching `SigningTarget` naming that fabricated `final_pubkey`'s fingerprint causes `execute_signing_instructions` to sign the target `message` using the real private key with the attacker-supplied `final_pubkey` as the Aug-scheme prefix — i.e., the wallet is coerced into producing a signature share bound to a key the caller invented rather than one it can prove ownership of [5](#0-4) .

### Citations

**File:** chia/wallet/wallet_signer.py (L172-196)
```python
        # Next, expand our pubkey set with sum hints
        sum_hint_lookup: dict[int, list[int]] = {}
        for sum_hint in signing_instructions.key_hints.sum_hints:
            fingerprints_we_have: list[int] = []
            for fingerprint in sum_hint.fingerprints:
                fingerprint_as_int = int.from_bytes(fingerprint, "big")
                if fingerprint_as_int not in pk_lookup:
                    if not partial_allowed:
                        raise ValueError(
                            f"No pubkey found (or path hinted to) for fingerprint {int.from_bytes(fingerprint, 'big')}"
                        )
                    else:
                        aggregate_responses_at_end = False
                else:
                    fingerprints_we_have.append(fingerprint_as_int)

            # Add any synthetic offsets as keys we "have"
            offset_sk = PrivateKey.from_bytes(sum_hint.synthetic_offset)
            offset_pk = offset_sk.get_g1()
            pk_lookup[offset_pk.get_fingerprint()] = offset_pk
            sk_lookup[offset_pk.get_fingerprint()] = offset_sk
            final_pubkey: G1Element = G1Element.from_bytes(sum_hint.final_pubkey)
            final_fingerprint: int = final_pubkey.get_fingerprint()
            pk_lookup[final_fingerprint] = final_pubkey
            sum_hint_lookup[final_fingerprint] = [*fingerprints_we_have, offset_pk.get_fingerprint()]
```

**File:** chia/wallet/wallet_signer.py (L198-218)
```python
        for target in signing_instructions.targets:
            pk_fingerprint: int = int.from_bytes(target.fingerprint, "big")
            if pk_fingerprint not in sk_lookup and pk_fingerprint not in sum_hint_lookup:
                if not partial_allowed:
                    raise ValueError(f"Pubkey {pk_fingerprint} not found (or path/sum hinted to)")
                else:
                    aggregate_responses_at_end = False
                    continue
            elif pk_fingerprint in sk_lookup:
                responses.append(
                    SigningResponse(
                        bytes(AugSchemeMPL.sign(sk_lookup[pk_fingerprint], target.message)),
                        target.hook,
                    )
                )
            else:  # Implicit if pk_fingerprint in sum_hint_lookup
                signatures: list[G2Element] = []
                for partial_fingerprint in sum_hint_lookup[pk_fingerprint]:
                    signatures.append(
                        AugSchemeMPL.sign(sk_lookup[partial_fingerprint], target.message, pk_lookup[pk_fingerprint])
                    )
```

**File:** chia/wallet/signer_protocol.py (L71-76)
```python
@clvm_streamable
@dataclass(frozen=True)
class SumHint(Streamable):
    fingerprints: list[bytes]
    synthetic_offset: bytes
    final_pubkey: bytes
```
