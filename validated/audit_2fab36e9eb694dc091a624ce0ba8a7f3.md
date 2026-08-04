### Title
Unauthenticated `address` field lets anyone steal relayer reputation rewards in `pallet-state-coprocessor::handle_get_requests` - (File: `modules/pallets/state-coprocessor/src/impls.rs`)

### Summary
`pallet-messaging-incentives` mints reward tokens ("reputation asset") only to the account that can be cryptographically recovered from the sr25519 signature on the delivered ISMP message, so the beneficiary is bound to the entity that actually did the relaying work. `pallet-state-coprocessor::handle_get_requests` — which mints from the very same `MintPerByte` rate and the very same `ReputationAsset` — instead trusts a raw, caller-supplied `address: Vec<u8>` field with no cryptographic binding to who produced or delivered the proof. Because the underlying extrinsic is unsigned (`ensure_none`) and permissionless, any unprivileged actor can resubmit a legitimate, publicly-verifiable state proof with their own account in `address` and mint the reward for themselves.

### Finding Description
Two code paths mint the same reputation reward using the same rate but with inconsistent authentication of the beneficiary:

- `pallet-messaging-incentives` derives the relayer strictly from the signature on the message: [1](#0-0) 

- `pallet-state-coprocessor::handle_get_requests` instead mints directly to whatever `address` the caller included in the extrinsic payload, with no signature check tying it to the proof's origin: [2](#0-1) 

The extrinsic that reaches this code is unsigned and permissionless: [3](#0-2) 

The only anti-replay protection is a `response_receipt` dedup check against the underlying `GetRequest`, not against who submits the batch: [4](#0-3) 

All inputs needed to build a valid `GetRequestsWithProof` — the `GetRequest`s, the source-chain membership proof, and the destination-chain state proof — are public chain data; nothing in them identifies the submitter as the party who "did the work" of serving the request. Consequently, whoever's `handle_unsigned` transaction lands first (whether by racing, observing another party's mempool submission, or independently fetching the same public proofs) collects the reputation mint under an `address` of their own choosing, regardless of who actually retrieved/relayed the underlying data. This breaks the "reward only the rightful beneficiary" invariant that `pallet-messaging-incentives` explicitly enforces via signature recovery.

### Impact Explanation
This is a false/wrong-beneficiary vulnerability in a reward-minting path reachable by an unprivileged, unauthenticated caller (`ensure_none`). Because the extrinsic is unsigned, it can be submitted by anyone with no economic or reputational identity check, directly diverting relayer incentive rewards away from the actual relayer/prover that performed the GET-request proof retrieval. The `ReputationAsset` is soulbound (transfers are call-filtered per the messaging-incentives test suite), so the only path to legitimately obtain it is minting — making this address-spoofing bug the direct mechanism for unauthorized value transfer of the reward asset.

### Likelihood Explanation
High: `handle_unsigned` requires no signature, no origin check beyond `ensure_none`, and no relationship between `address` and the proof content. Any node capable of observing a pending `GetRequestsWithProof` transaction (which is `propagate: true` in the transaction pool) or independently constructing one from public consensus/state proofs can substitute their own account and submit it first.

### Recommendation
Bind the reward beneficiary in `pallet-state-coprocessor` the same way `pallet-messaging-incentives` does — require the `address` to be authenticated (e.g. require the extrinsic to be signed by the claiming account, or require a signature over the batch that is verified and recovered the way `relayer_for` does in `pallet-messaging-incentives`) — rather than trusting an unauthenticated `Vec<u8>` supplied by the caller of an unsigned, permissionless extrinsic.

### Proof of Concept
1. Observe (or independently derive) a valid `GetRequestsWithProof` for pending Get requests — including `requests`, `source` proof, and `response` proof — all of which are public data.
2. Submit `state-coprocessor::handle_unsigned(origin: None, message: GetRequestsWithProof { ..., address: <attacker_account_bytes32> })` before the legitimate relayer's equivalent submission lands (the tx pool `propagate: true` and `ensure_none` origin allow any node to do this).
3. `validate_unsigned`/`handle_get_requests` executes: proofs verify (they are legitimately valid), `response_receipt` has not yet been written for these requests, so no `DuplicateResponse` error occurs.
4. `ReputationAsset::mint_into(&attacker_account, amount)` mints the full byte-proportional reward to the attacker instead of whoever actually retrieved the proof data, per [5](#0-4) .

### Citations

**File:** modules/pallets/messaging-incentives/src/lib.rs (L137-153)
```rust
	/// Recover the relayer's account from the sr25519 signature on a
	/// `Message`'s `signer` field. Returns `None` if the message has
	/// no signer (e.g. consensus messages) or the signature is bad.
	fn relayer_for(message: &Message) -> Option<T::AccountId> {
		let (signer, signed) = match message {
			Message::Request(msg) =>
				(&msg.signer, sp_io::hashing::keccak_256(&msg.requests.encode())),
			Message::Response(msg) =>
				(&msg.signer, sp_io::hashing::keccak_256(&msg.requests.encode())),
			_ => return None,
		};
		Signature::decode(&mut &signer[..])
			.ok()?
			.verify_and_get_sr25519_pubkey(&signed, None)
			.ok()
			.map(T::AccountId::from)
	}
```

**File:** modules/pallets/state-coprocessor/src/impls.rs (L94-103)
```rust
			// This request has already been responded to. Mirror `handlers/response.rs:61`:
			// dedup against `response_receipt`, which the dispatch path writes for this exact
			// GetRequest hash after producing a response. The receipt also binds the response
			// commitment, so external auditors can attest "Hyperbridge produced response X for
			// request Y" from one map.
			let probe = GetResponse { get: req.clone(), values: Default::default() };
			if host.response_receipt(&probe).is_some() {
				Err(Error::DuplicateResponse { meta: (&probe).into() })?
			}
		}
```

**File:** modules/pallets/state-coprocessor/src/impls.rs (L157-186)
```rust
		// Mint reputation tokens to the named relayer. The address is the
		// relayer's raw 32-byte public key as supplied by the coprocessor.
		// A zero rate disables minting and a malformed address simply skips
		// the mint — we don't want a non-32-byte address to fail the whole
		// batch since the response insertion below has no dependency on it.
		// The per-byte rate and reputation asset are inherited from
		// `pallet-messaging-incentives` so both pallets share one source of truth.
		let rate = pallet_messaging_incentives::MintPerByte::<T>::get();
		if !rate.is_zero() && total_bytes > 0 {
			if let Ok(bytes32) = <[u8; 32]>::try_from(address.as_slice()) {
				let relayer: T::AccountId = bytes32.into();
				let bytes_balance: BalanceOf<T> = (total_bytes as u128).saturated_into();
				let amount = rate.saturating_mul(bytes_balance);
				if !amount.is_zero() {
					match <T as pallet_messaging_incentives::Config>::ReputationAsset::mint_into(
						&relayer, amount,
					) {
						Ok(_) => Pallet::<T>::deposit_event(Event::ReputationMinted {
							relayer,
							bytes: total_bytes,
							amount,
						}),
						Err(err) => log::warn!(
							target: "ismp",
							"state-coprocessor: reputation mint failed for {total_bytes}b: {err:?}",
						),
					}
				}
			}
		}
```

**File:** modules/pallets/state-coprocessor/src/lib.rs (L90-104)
```rust
		#[pallet::call_index(0)]
		#[pallet::weight(<T as frame_system::Config>::DbWeight::get().reads_writes(1, 2))]
		pub fn handle_unsigned(
			origin: OriginFor<T>,
			message: GetRequestsWithProof,
		) -> DispatchResult {
			ensure_none(origin)?;

			Self::handle_get_requests(message).map_err(|err| {
				log::error!(target: "ismp", "pallet-coprocessor: {:?}", err);
				Error::<T>::HandlingError
			})?;

			Ok(())
		}
```
