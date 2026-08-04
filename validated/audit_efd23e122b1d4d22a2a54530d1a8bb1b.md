### Title
BEEFY consensus verifier counts raw signature-array length instead of distinct authority indices, letting a minority (or single) signer forge supermajority finality - ([File: modules/consensus/beefy/verifier/src/lib.rs])

### Summary
Chronicle's `opPoke` bug let an attacker inflate an array (`schnorrData.feedIds`) beyond what the protocol's implicit invariant (`length == bar`) allowed, breaking the economic/gas assumptions of the code that consumed it. The same bug *class* — trusting a raw array length as a proxy for a semantically distinct set, without validating uniqueness/composition of its elements — exists in Hyperbridge's BEEFY consensus verifier, where `signatures.len()` is used as the supermajority signer count without ever checking that the `index` field of each signature is unique.

### Finding Description
`verify_mmr_update_proof` computes the number of participating validators purely from the length of the submitted signature array: [1](#0-0) 

`check_participation_threshold` only compares `signatures_length` (i.e. `mmr.signed_commitment.signatures.len()`) against `(2*total)/3 + 1`: [2](#0-1) 

The subsequent loop builds `authority_leaves`/`authority_indices` directly from the (unvalidated) signature list, one entry per signature, with **no deduplication check on `sig.index`**: [3](#0-2) 

Because `secp256k1_recover` only verifies that a signature is cryptographically valid for *some* key — it does not verify that the key is distinct from other entries in the list — an attacker who controls (or has previously obtained) a single valid BEEFY signature from one authority over a commitment can duplicate that exact `(index, signature)` pair `N` times in `mmr.signed_commitment.signatures` to make `signatures_length` reach the `(2/3)*total + 1` threshold. The subsequent `merkle_proof.verify(..., &authority_indices, &authority_leaves, ...)` call is presented with the same `(index, leaf)` pair repeated many times, which is internally consistent (the leaf at that index in the real merkle tree genuinely equals that value), so the multi-proof verification succeeds even though only one real signer contributed.

This mirrors the Scribe `opPoke` root cause exactly: the code enforces a *count* derived from an attacker-controlled array's length as a stand-in for a "distinct-participant" invariant, without validating that the array's elements are actually distinct/well-formed for that purpose.

### Impact Explanation
This directly violates the pivot: *"Consensus proofs, state proofs, challenge periods, and state commitments must never let false remote state become trusted."* If exploitable, a single byzantine (or previously-compromised) relay-chain validator key — far below the honest 2/3+1 BFT assumption — can forge acceptance of an arbitrary MMR root / parachain header set as finalized Hyperbridge consensus state. Since all downstream request/response/timeout processing (`HandlerV2.sol`, ISMP state machines) trusts state commitments derived from this consensus proof, this would let an attacker root arbitrary false state, enabling forged request/response delivery, incorrect timeouts, and ultimately fund loss across every application relying on the affected consensus client — a "false proof/state acceptance" bug in the accepted-impact list.

### Likelihood Explanation
Exploitability hinges on whether the underlying `rs_merkle::MerkleProof::verify` implementation actually tolerates duplicate `(index, leaf)` pairs within a single multiproof verification call as internally consistent (i.e., does not reject a proof/leaf-set with repeated indices). I was not able to fully confirm this behavior from the code available in the index — the `rs_merkle` crate internals are external and not present in this repository's indexed content, and no local test in `modules/consensus/beefy/verifier/src/test.rs` explicitly covers the duplicate-index case (only a partial match was found there, and I could not read its content within my remaining tool budget). If `rs_merkle` internally deduplicates/sorts indices and would fail or reduce the effective participation count when duplicates are supplied, this exact path would not be exploitable, and the flaw would be limited to relying on `check_participation_threshold` alone, which is the vulnerable component regardless. This is a real gap in the code's business logic (participation count is never cross-checked against unique authority identity) even if the merkle library happens to defensively neutralize the abuse — that mitigation, if it exists, is incidental, not an intentional protocol guarantee.

### Recommendation
- Deduplicate `authority_indices` (and reject the whole proof if duplicates are found) before computing `signatures_length` for the participation-threshold check, so the count used in `check_participation_threshold` reflects unique authority indices, not raw array length.
- Alternatively, sort and require strict monotonic increase of `sig.index` across `mmr.signed_commitment.signatures`, which both prevents duplicates and matches the well-known "sorted-unique-indices" invariant multi-proof verifiers typically expect.
- Add an explicit unit test asserting that a `ConsensusMessage` with `signatures_length >= threshold` built entirely from duplicated `(index, signature)` pairs is rejected.

### Proof of Concept
Conceptual PoC (cannot be fully executed without the `rs_merkle` crate source, which is external to this repo):
1. Obtain (or produce, e.g., via a colluding/compromised authority) one valid BEEFY ECDSA signature `sig` over a commitment `C`, with `sig.index = k` for authority `k` in the current authority set of size `total`.
2. Construct `mmr.signed_commitment.signatures = [sig; N]` where `N = (2*total)/3 + 1`, i.e., copy the single valid signature `N` times.
3. Submit this `ConsensusMessage` to `verify_consensus` / `verify_mmr_update_proof`.
4. `check_participation_threshold(N, total)` passes since `N >= (2*total)/3 + 1`.
5. The loop recovers the same public key `N` times, producing `authority_indices = [k; N]` and `authority_leaves = [leaf_k; N]`.
6. If `merkle_proof.verify(root, &[k;N], &[leaf_k;N], total)` returns `true` for this degenerate, duplicated multiproof, the commitment `C` (an attacker/single-authority-chosen MMR root and associated parachain headers) is accepted as finalized state despite only one real signer.

Confirming step 6's outcome against the actual `rs_merkle` implementation used by this repo requires deeper library-level verification than the indexed codebase context allows; a Devin session with full repository and dependency access should reproduce this end-to-end (unit test against `verify_mmr_update_proof` with a synthetic authority set and duplicated signature) to conclusively confirm or rule out exploitability.

### Citations

**File:** modules/consensus/beefy/verifier/src/lib.rs (L109-133)
```rust
	let signatures_length = mmr.signed_commitment.signatures.len();
	let latest_height = mmr.signed_commitment.commitment.block_number;

	if trusted_state.latest_beefy_height >= latest_height {
		return Err(Error::StaleHeight {
			trusted_height: trusted_state.latest_beefy_height,
			current_height: latest_height,
		});
	}

	let commitment = mmr.signed_commitment.commitment.clone();

	// Pick the authority set the commitment claims to be signed under, then judge
	// participation against that set alone.
	let authority_set = if commitment.validator_set_id == trusted_state.current_authorities.id {
		&trusted_state.current_authorities
	} else if commitment.validator_set_id == trusted_state.next_authorities.id {
		&trusted_state.next_authorities
	} else {
		return Err(Error::UnknownAuthoritySet { id: commitment.validator_set_id });
	};

	if !check_participation_threshold(signatures_length as u32, authority_set.len) {
		return Err(Error::SuperMajorityRequired);
	}
```

**File:** modules/consensus/beefy/verifier/src/lib.rs (L145-171)
```rust
	let commitment_hash = H::keccak256(&commitment.encode());
	let mut authority_leaves: Vec<[u8; 32]> = Vec::new();
	let mut authority_indices = Vec::new();

	for sig in mmr.signed_commitment.signatures.iter() {
		let uncompressed = H::secp256k1_recover(&commitment_hash.0, &sig.signature)
			.map_err(|_| Error::FailedToRecoverPublicKey)?;

		let hashed_uncompressed = H::keccak256(&uncompressed);

		let mut eth_address = [0u8; 20];
		eth_address.copy_from_slice(&hashed_uncompressed.as_ref()[12..]);

		let authority_address_hash = H::keccak256(&eth_address);

		authority_leaves.push(authority_address_hash.into());
		authority_indices.push(sig.index as usize);
	}

	let merkle_proof = MerkleProof::<MerkleHasher<H>>::new(mmr.authority_proof.clone());

	let valid = merkle_proof.verify(
		authority_set.keyset_commitment.into(),
		&authority_indices,
		&authority_leaves,
		authority_set.len as usize,
	);
```

**File:** modules/consensus/beefy/verifier/src/lib.rs (L258-261)
```rust
/// Checks for supermajority participation
fn check_participation_threshold(len: u32, total: u32) -> bool {
	len >= ((2 * total) / 3) + 1
}
```
