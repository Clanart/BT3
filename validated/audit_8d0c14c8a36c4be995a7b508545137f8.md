Confirmed: `verify_sp1_consensus` in `modules/consensus/beefy/verifier/src/sp1.rs` decodes/hashes `proof.headers` and returns them unconditionally in `Ok((new_state.encode(), proof.headers))` with no check on the decoded header's block number. [1](#0-0) 

The Solidity equivalent explicitly decodes each header and reverts with `IllegalGenesisBlock` if `header.number == 0`, before constructing the `IntermediateState`. [2](#0-1) 

Tracing the downstream consumer, `BeefyConsensusClient::verify_consensus` in `modules/ismp/clients/beefy/src/consensus.rs` takes each `para_header` returned by `verify_sp1_consensus`, decodes it as a `Header`, and unconditionally builds a `StateCommitmentHeight` from `header.number()`, `header.state_root`, and digest-derived `timestamp`/`overlay_root` — there is no genesis-height (`number == 0`) guard anywhere in this loop. [3](#0-2) 

### Title
Missing genesis-height check in `verify_sp1_consensus` allows genesis parachain headers to be accepted as fresh intermediate state - (File: modules/consensus/beefy/verifier/src/sp1.rs)

### Summary
`verify_sp1_consensus` mirrors the Solidity `SP1Beefy.verifyConsensus` flow for authority/leaf/nonce checks but omits the `header.number == 0` guard (`IllegalGenesisBlock` in Solidity). This asymmetry lets a header decoding to block 0 pass through `verify_sp1_consensus` and be accepted by `BeefyConsensusClient::verify_consensus` as a valid `StateCommitmentHeight`.

### Finding Description
The Rust `verify_sp1_consensus` function performs staleness, mmr-leaf-freshness, and authority-set checks, then computes header hashes for the SP1 public inputs and returns `proof.headers` verbatim on success — it never inspects the decoded header number. [4](#0-3) [5](#0-4) 

The Solidity `SP1Beefy.verifyConsensus`, which this Rust code documents itself as mirroring ("Mirrors the Solidity `SP1Beefy.verifyConsensus` flow"), explicitly decodes each header and reverts if `header.number == 0` before emitting the `IntermediateState`. [6](#0-5) 

Note however that SP1 verification only proves that the header hash matches the leaf's committed header inclusion and that the leaf is *in* the MMR (per the file's own doc comment); it does not attest that the header's `number` field is non-zero or anything else about header semantics beyond the hash matching what's proven. Since the leaf-freshness check (`parent_number + 1 == block_number`) constrains the *relay chain* block number, not the parachain header's own `number` field, a parachain header with `number == 0` embedded in `proof.headers` can still pass SP1 proof verification as long as its hash matches what was proven — the SP1 circuit does not appear to constrain the parachain header's internal `number` field, only that it's included at a given relay-chain leaf.

### Impact Explanation
If accepted, `BeefyConsensusClient::verify_consensus` decodes this header and unconditionally inserts a `StateCommitmentHeight` keyed by `StateMachineId` derived from `para_header.para_id`, using `header.state_root` and digest-derived `timestamp`/`overlay_root` from that header, with `height` taken from `header.number()` (which would be 0). [7](#0-6) 
This corrupts `StateCommitmentHeight` bookkeeping for the tracked parachain with genesis-block state (state_root/timestamp of block 0), which downstream request/response proof verification (via `StateMachineClient` state-proof paths) would then trust as a legitimate intermediate state for that height, potentially enabling stale/incorrect state roots to be used for validating cross-chain request/response commitments.

### Likelihood Explanation
Exploitability depends entirely on whether the SP1 zero-knowledge circuit (off-chain, not in this repo) actually constrains/rejects a genesis header being embedded in `proof.headers`. Since the on-chain/on-Rust side does no such check, if the circuit doesn't independently enforce it, an unprivileged party crafting a valid SP1 proof containing a genesis header for a real included parachain block would pass verification. This mirrors exactly why the Solidity side added an explicit belt-and-suspenders `IllegalGenesisBlock` check rather than relying solely on the circuit — the missing equivalent in Rust is a genuine parity gap.

### Recommendation
Add the same guard as Solidity's `IllegalGenesisBlock`: after decoding/mapping `proof.headers`, verify each header's parachain-level block number is non-zero (or perform this check in `BeefyConsensusClient::verify_consensus` after decoding `Header::<u32, BlakeTwo256>` before constructing `StateCommitmentHeight`), returning an error (e.g., a new `Error::IllegalGenesisBlock` variant) otherwise.

### Proof of Concept
1. Construct a `Sp1BeefyProof` whose `proof.headers` includes an entry for a tracked `para_id` whose SCALE-encoded `header` decodes to `number == 0`, with the SP1 Groth16 proof crafted (per the circuit's actual constraints) to validate MMR-leaf inclusion for that header hash.
2. Call `verify_sp1_consensus` — code inspection shows it does not check `h.header`'s decoded number field anywhere in the `.map()` over `proof.headers` at lines 84–91, nor after proof verification at lines 111–118, so it returns `Ok((.., proof.headers))` unmodified. [1](#0-0) 
3. `BeefyConsensusClient::verify_consensus` then decodes this header and inserts a `StateCommitmentHeight` at height 0 with the genesis `state_root`/`timestamp` for the tracked parachain, with no rejection anywhere in the loop. [8](#0-7) 
4. Compare to Solidity: an equivalent proof submitted to `SP1Beefy.verifyConsensus` would revert with `IllegalGenesisBlock` at line 162 before ever constructing the `IntermediateState`. [9](#0-8)

### Citations

**File:** modules/consensus/beefy/verifier/src/sp1.rs (L84-118)
```rust
	let headers = proof
		.headers
		.iter()
		.map(|h| ParachainHeaderHash {
			id: U256::from(h.para_id),
			hash: FixedBytes::from(Into::<[u8; 32]>::into(H::keccak256(&h.header))),
		})
		.collect();

	let public_inputs = PublicInputs {
		authorities_root: FixedBytes::from(Into::<[u8; 32]>::into(authority.keyset_commitment)),
		authorities_len: U256::from(authority.len),
		leaf_hash: FixedBytes::from(Into::<[u8; 32]>::into(H::keccak256(&proof.mmr_leaf.encode()))),
		block_number: U256::from(proof.block_number),
		headers,
		nonce: FixedBytes::from(proof.nonce.0),
	}
	.abi_encode();

	sp1_verifier::Groth16Verifier::verify(
		&proof.proof,
		&public_inputs,
		vkey,
		sp1_verifier::GROTH16_VK_BYTES,
	)
	.map_err(|_| Error::Sp1VerificationFailed)?;

	let mut new_state = trusted_state;
	if proof.mmr_leaf.beefy_next_authority_set.id > new_state.next_authorities.id {
		new_state.current_authorities = new_state.next_authorities.clone();
		new_state.next_authorities = proof.mmr_leaf.beefy_next_authority_set.clone();
	}
	new_state.latest_beefy_height = proof.block_number;

	Ok((new_state.encode(), proof.headers))
```

**File:** evm/src/consensus/SP1Beefy.sol (L157-168)
```text
        uint256 statesLen = proof.headers.length;
        IntermediateState[] memory intermediates = new IntermediateState[](statesLen);
        for (uint256 i = 0; i < statesLen; i++) {
            ParachainHeader memory para = proof.headers[i];
            Header memory header = Codec.DecodeHeader(para.header);
            if (header.number == 0) revert IllegalGenesisBlock();

            StateCommitment memory stateCommitment = header.stateCommitment();
            IntermediateState memory intermediate =
                IntermediateState({stateMachineId: para.id, height: header.number, commitment: stateCommitment});
            intermediates[i] = intermediate;
        }
```

**File:** modules/ismp/clients/beefy/src/consensus.rs (L114-172)
```rust
		for para_header in verified_parachains {
			// Skip parachains not tracked by this consensus client
			if !C::is_parachain_tracked(para_header.para_id) {
				continue;
			}

			let header = Header::<u32, BlakeTwo256>::decode(&mut &*para_header.header)
				.map_err(|e| BeefyError::DecodeParachainHeader(format!("{e}")))?;

			let mut state_commitments_vec = Vec::new();
			let (mut timestamp, mut overlay_root) = (0, H256::default());

			for digest in header.digest().logs.iter() {
				match digest {
					DigestItem::Consensus(consensus_engine_id, value)
						if *consensus_engine_id == ISMP_TIMESTAMP_ID =>
					{
						let timestamp_digest = TimestampDigest::decode(&mut &value[..])
							.map_err(|e| BeefyError::DecodeTimestampDigest(format!("{e:?}")))?;
						timestamp = timestamp_digest.timestamp;
					},
					DigestItem::Consensus(consensus_engine_id, value)
						if *consensus_engine_id == ISMP_ID =>
					{
						let log = ConsensusDigest::decode(&mut &value[..]);
						if let Ok(log) = log {
							overlay_root = log.child_trie_root;
						} else {
							Err(BeefyError::InvalidIsmpConsensusLog)?
						}
					},
					_ => {},
				};
			}
			if timestamp == 0 {
				Err(BeefyError::TimestampNotFound)?
			}

			let (state_id, consensus_state_id) = match host.host_state_machine() {
				StateMachine::Kusama(_) =>
					(StateMachine::Kusama(para_header.para_id), PASEO_CONSENSUS_STATE_ID),
				StateMachine::Polkadot(_) =>
					(StateMachine::Polkadot(para_header.para_id), POLKADOT_CONSENSUS_STATE_ID),
				_ => Err(BeefyError::HostStateMachineNotParachain)?,
			};

			let height: u32 = (*header.number()).into();
			let intermediate = StateCommitmentHeight {
				commitment: StateCommitment {
					timestamp,
					overlay_root: Some(overlay_root),
					state_root: header.state_root,
				},
				height: height.into(),
			};

			state_commitments_vec.push(intermediate);
			intermediates
				.insert(StateMachineId { state_id, consensus_state_id }, state_commitments_vec);
```
