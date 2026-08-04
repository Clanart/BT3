## Title
Relayer reward permanently unclaimable when `EvmHost._epochs[set_id]` address has a leading zero byte due to non-dynamic RLP address decoding — (File: `modules/pallets/relayer/src/outbound_consensus.rs`)

## Summary

`Pallet::decode_epochs_slot_address`, used by `process_outbound_consensus_delivery_claim` to attribute and pay the `OutboundConsensusDeliveryReward`, relies on `alloy_rlp::Address::decode`, which requires an RLP string of exactly 20 payload bytes. [1](#0-0) 

## Finding Description

The reward attribution reads the raw trie value at `EvmHost._epochs[set_id]` (populated once on-chain by `HandlerV2.handleConsensus`/`EvmHost.recordEpoch`, and never rewritten afterward) and decodes it to an `address` via `decode_epochs_slot_address`. That function only accepts the value if `alloy_rlp::Address::decode` succeeds — which, per RLP rules, requires the value to be exactly the 20-byte string form (`0x94` + 20 bytes). Ethereum state tries strip leading zero bytes before RLP-encoding storage values, so if the recording relayer's address happens to have a zero top byte, the trie stores only 19 non-zero bytes with prefix `0x93`, which `Address::decode` rejects as malformed. This exact behavior is confirmed and pinned by the repository's own regression tests: [2](#0-1) 

When decoding fails, `decode_epochs_slot_address` returns `None`, which `process_outbound_consensus_delivery_claim` maps to `Error::OutboundDeliveryNotProven` — the same error used for a genuinely unset slot: [3](#0-2) 

Because the on-chain slot value at `_epochs[set_id]` is fixed once written (it is never rewritten with a different byte pattern), this failure is not retryable: the underlying trie value permanently encodes as a 19-byte RLP string, so the claim path can never succeed for that `(destination, set_id)`.

## Impact Explanation

Any relayer whose EVM address (the `msg.sender` that submitted the qualifying mandatory consensus proof) has a leading zero byte is permanently unable to claim the configured `OutboundConsensusDeliveryReward` for that `(destination, set_id)`. The reward stays locked in the treasury for that claim slot, since `OutboundConsensusRotationsClaimed` is never set (the claim never succeeds) yet the funds tied to this attribution path can never be released to the rightful relayer — a direct loss/lock-of-funds condition in the reward-claim logic named in the bounty scope.

## Likelihood Explanation

Address leading-zero-byte probability is ~1/256 (~0.4%) per relayer address, matching the exact probability class described in the seed report and explicitly acknowledged in the codebase's own comments as an accepted trade-off: [4](#0-3) 

This is not a contrived edge case — it is a naturally occurring condition for a fraction of legitimately generated relayer signer keys, triggered purely by permissionless submission of a valid `OutboundConsensusDeliveryClaim`.

## Recommendation

Replace the fixed-20-byte `Address::decode` requirement in `decode_epochs_slot_address` with a leading-zero-tolerant decoder that RLP-decodes the byte string and left-pads to 20 bytes before reconstructing the `Address`, mirroring the padding approach already used elsewhere in the codebase (e.g., `modules/consensus/pharos/verifier/src/state_proof.rs`'s `padded[32 - value.len()..]` pattern) rather than requiring an exact-length RLP string.

## Proof of Concept

Using the repository's own test as the reproduction: [5](#0-4) 

For a relayer address with a zero top byte, the EVM trie stores it as a 19-byte RLP string (prefix `0x93`); `decode_epochs_slot_address` returns `None`, and `process_outbound_consensus_delivery_claim` (`modules/pallets/relayer/src/outbound_consensus.rs:164-165`) rejects the claim with `OutboundDeliveryNotProven` for every subsequent call, since the underlying proven value never changes.

### Citations

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L159-165)
```rust
		// `raw` is the trie-level value of `EvmHost._epochs[set_id]`;
		// `decode_epochs_slot_address` handles the RLP-encoded form the
		// Ethereum trie stores. Returns `None` for an unset / zero-address
		// slot, which we surface as `OutboundDeliveryNotProven` (logically
		// equivalent to "no delivery proven yet").
		let evm_address = Self::decode_epochs_slot_address(destination, &raw)
			.ok_or(Error::<T>::OutboundDeliveryNotProven)?;
```

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L200-216)
```rust
	/// Decode the `address` value from `EvmHost._epochs[set_id]` as returned
	/// by `EvmStateMachine::verify_state_proof`. Standard EVM chains RLP-encode
	/// the value; Pharos stores it as a raw 32-byte ABI-padded word.
	pub fn decode_epochs_slot_address(
		state_id: ismp::host::StateMachine,
		raw: &[u8],
	) -> Option<Address> {
		use alloy_rlp::Decodable;
		if let Ok(addr) = Address::decode(&mut &*raw) {
			return if addr == Address::ZERO { None } else { Some(addr) };
		}
		if crate::is_pharos(&state_id) && raw.len() == 32 {
			let addr = Address::from_slice(&raw[12..]);
			return if addr == Address::ZERO { None } else { Some(addr) };
		}
		None
	}
```

**File:** modules/pallets/testsuite/src/tests/pallet_ismp_relayer.rs (L1133-1154)
```rust
		/// `alloy_rlp::Address::decode` requires exactly 20 payload
		/// bytes after the RLP header. The EVM strips leading zero
		/// bytes from values before RLP-encoding, so an address whose
		/// top byte is zero shows up as `<21` bytes total and decodes
		/// to an error. Relayer addresses are random 20-byte values
		/// generated at signer keygen, so the chance of a leading
		/// zero byte is ~1/256 and we accept this trade-off in
		/// exchange for a single-line decoder. This test pins the
		/// behaviour: if/when we change to a leading-zero-tolerant
		/// decoder, flip this to expect Some.
		#[test]
		fn rejects_address_with_leading_zero_stripped() {
			let mut addr_no_leading = [0u8; 20];
			addr_no_leading[1..].copy_from_slice(&RELAYER_ADDR[1..]); // top byte stays 0
			let stripped = &addr_no_leading[1..]; // 19 bytes (leading zero gone)
			let mut raw = vec![0x80 + stripped.len() as u8];
			raw.extend_from_slice(stripped);

			assert!(
				pallet_ismp_relayer::Pallet::<Test>::decode_epochs_slot_address(ismp::host::StateMachine::Evm(1), &raw).is_none(),
			);
		}
```
