## Analysis

The HolographOperator bug reduces to one invariant: **a reward/slash credit is attributed to an address by protocol logic, but the withdrawal path can only pay out to an address that can prove control via a specific authorization mechanism the credited address structurally cannot satisfy** — so the funds become permanently unclaimable.

Hyperbridge has a direct analog in the relayer-fee attribution/withdrawal pipeline.

### Where the credit is attributed
`HandlerV2.handlePostRequests` records the delivering "relayer" as `_msgSender()` with no restriction that it be an EOA: [1](#0-0) 

`EvmHost.dispatchIncoming` stores that raw address into `_requestReceipts[commitment]`: [2](#0-1) 

`pallet-ismp-relayer::decode_receipt_relayer` then decodes this EVM receipt purely as 20 raw address bytes, with no check that the address is an EOA: [3](#0-2) 

`accumulate_fees` credits `Fees<T>[state_machine][delivery_address]` (or a signed-redirect `beneficiary_address`) using this exact byte value as the map key: [4](#0-3) 

### Where the credit becomes unclaimable
`withdraw_fees` is the only way to move `Fees<T>` out, and it strictly requires an ECDSA/sr25519/ed25519 signature that recovers to *exactly* the stored address bytes: [5](#0-4) 

The only escape hatch — redirecting the beneficiary before accumulation — also requires a signature that must verify against `delivery_address`: [6](#0-5) 

### The broken invariant
Nothing in `handlePostRequests`/`dispatchIncoming`/`decode_receipt_relayer` requires that the address credited as "delivering relayer" be an externally-owned account capable of producing an ECDSA/sr25519/ed25519 signature. Any contract-based caller of `handlePostRequests` (a Safe/multisig, a Gelato/Defender-style relay-forwarder contract, a batching proxy, an ERC-4337-adjacent relayer wallet — all standard infrastructure patterns for automated relayer operations) becomes `_msgSender()`, gets recorded as the relayer, and accrues real fees into `Fees<T>[chain][contract_address]`. Since a smart-contract address has no private key, `withdraw_fees` can never produce a valid signature recovering to that address, and the redirect path in `accumulate` has the identical requirement. The fee balance is permanently locked — exactly the HLG-style "credited but structurally unclaimable" bug class, except here it requires no exploit at all: any relayer operator using contract-based signing infrastructure self-locks their own earned fees, and there's no admin/governance recovery path for this map entry.

### Title
Relayer fees permanently locked when `RequestReceipts` records a smart-contract address as the delivering relayer - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`pallet-ismp-relayer` credits accrued relayer fees to whatever raw address bytes `EvmHost._requestReceipts[commitment]` records as the delivering relayer, which is simply `_msgSender()` of the EVM `handlePostRequests` call with no EOA-only restriction. Withdrawal (`withdraw_fees`) and the beneficiary-redirect path in `accumulate` both require the credited address to produce an ECDSA/sr25519/ed25519 signature that recovers to itself. A contract address (Safe, relay-forwarder, batching proxy) can never do this, so any fees accrued to it are permanently stranded in `Fees<T>`.

### Finding Description
1. `HandlerV2.handlePostRequests` calls `host.dispatchIncoming(leaf.request, _msgSender())`, with `_msgSender()` being whatever address (EOA or contract) submitted the transaction/batch. [1](#0-0) 
2. `EvmHost.dispatchIncoming` stores this address unconditionally into `_requestReceipts[commitment]`, with no `extcodesize` or EOA check. [7](#0-6) 
3. `pallet-ismp-relayer`'s `decode_receipt_relayer` decodes the EVM receipt slot as plain address bytes for any EVM destination. [8](#0-7) 
4. `accumulate` credits `Fees<T>[state_machine][delivery_address]` using that value directly as the storage key, or (if a beneficiary redirect is attached) requires `delivery_address` to sign the redirect message. [4](#0-3) 
5. `withdraw_fees` requires a signature (`Signature::Evm`/`Sr25519`/`Ed25519`) whose recovered address/pubkey equals the stored key exactly. [5](#0-4) 

If the credited address is a smart contract, no valid signature can ever be produced for either the direct withdrawal or the redirect, so the accrued balance in `Fees<T>` is permanently unreachable — the funds are neither refunded, sweepable by governance, nor otherwise recoverable in the shown code paths.

### Impact Explanation
This is a direct loss-of-funds bug: legitimately earned relayer fees (paid by users/apps to incentivize message delivery) become permanently locked in pallet storage whenever the delivering address recorded on the EVM destination is a contract rather than an EOA. This is not a hypothetical edge case — contract-based relaying (Safe multisigs, Gelato/OpenZeppelin Defender-style relayer wallets, custom batching/forwarder contracts) is a normal operational pattern for automated relayer infrastructure. No admin/governance recovery mechanism for stuck `Fees<T>` entries is present in the reviewed code.

### Likelihood Explanation
High likelihood of occurrence in normal operation, and zero privilege is required to trigger it — the `handlePostRequests`/`batchCall` entrypoints are unauthenticated with respect to caller type, so any relayer choosing (or being configured, e.g. via standard relay-as-a-service tooling) to submit through a smart-contract wallet will hit this. It requires no malicious peer, relayer, prover, or admin — it is a straightforward consequence of the protocol never checking that the credited "relayer" address can actually produce a signature.

### Recommendation
- Reject (or separately flag) `dispatchIncoming` / `handlePostRequests` submissions where `_msgSender()` has non-zero `extcodesize`, so only EOA relayers get credited directly; or
- Add a governance/sweep path in `pallet-ismp-relayer` to recover `Fees<T>` entries keyed by addresses that can be proven unable to sign (e.g. via an on-chain code-size proof), redirecting them to a claimable account; or
- Change the fee-credit path so the credited beneficiary is chosen at delivery time from a value the destination-chain caller supplies and can prove (e.g. an EIP-712 signed beneficiary embedded in the calldata) rather than being implicitly `msg.sender`.

### Proof of Concept
1. Deploy an arbitrary contract `RelayForwarder` with a function that calls `HandlerV2.handlePostRequests(host, request)` (or `batchCall`).
2. Have `RelayForwarder` submit a valid post-request delivery. `EvmHost.dispatchIncoming` records `_requestReceipts[commitment] = address(RelayForwarder)`.
3. A relayer submits a `WithdrawalProof` to `pallet-ismp-relayer::accumulate_fees` proving this delivery; `Fees::<T>::get(state_machine, address(RelayForwarder))` is credited with the fee amount (`accumulate.rs` lines 106–147).
4. Attempt `withdraw_fees` with any `Signature::Evm{address: address(RelayForwarder), signature}` — no ECDSA signature can ever recover to a contract address, so `withdrawal.rs`'s `signature.verify(...)` will never match, and `Error::InvalidPublicKey`/`InvalidSignature` is returned unconditionally.
5. The fee balance for `(state_machine, address(RelayForwarder))` remains in `Fees<T>` forever, with no code path to redirect or recover it.

### Citations

**File:** evm/src/core/HandlerV2.sol (L204-209)
```text
        for (uint256 i = 0; i < requestsLen; ++i) {
            PostRequestLeaf memory leaf = request.requests[i];
            // duplicate request?
            if (host.requestReceipts(leaf.request.hash()) != address(0)) revert DuplicateMessage();
            host.dispatchIncoming(leaf.request, _msgSender());
        }
```

**File:** evm/src/core/EvmHost.sol (L794-818)
```text
    function dispatchIncoming(PostRequest memory request, address relayer) external restrict(_hostParams.handler) {
        address destination = _bytesToAddress(request.to);
        uint256 size;
        assembly {
            size := extcodesize(destination)
        }
        if (size == 0) {
            // instead of reverting the entire batch, early return here.
            return;
        }

        // replay protection
        bytes32 commitment = request.hash();
        _requestReceipts[commitment] = relayer;

        (bool success,) = address(destination)
            .call(abi.encodeWithSelector(IApp.onAccept.selector, IncomingPostRequest(request, relayer)));

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
    }
```

**File:** modules/pallets/relayer/src/accumulate.rs (L106-147)
```rust
		// Let's verify the beneficiary address
		let beneficiary_address = if let Some((beneficiary_address, signature)) =
			withdrawal_proof.beneficiary_details
		{
			let nonce = Nonce::<T>::get(&delivery_address, state_machine);
			let msg = beneficiary_message(nonce, state_machine, &beneficiary_address);
			match &signature {
				Signature::Evm { .. } => {
					let eth_address =
						signature.verify(&msg, None).map_err(|_| Error::<T>::InvalidSignature)?;
					if eth_address != delivery_address {
						Err(Error::<T>::InvalidPublicKey)?
					}
				},
				Signature::Sr25519 { .. } | Signature::Ed25519 { .. } => {
					// verify the signature with the delivery address from the state proof
					let _ = signature
						.verify(&msg, Some(delivery_address.clone()))
						.map_err(|_| Error::<T>::InvalidSignature)?;
				},
			}

			Nonce::<T>::try_mutate(&delivery_address, state_machine, |value| {
				*value += 1;
				Ok::<(), ()>(())
			})
			.map_err(|_: ()| Error::<T>::ErrorCompletingCall)?;

			let _ = Fees::<T>::try_mutate(state_machine, beneficiary_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			beneficiary_address
		} else {
			let _ = Fees::<T>::try_mutate(state_machine, delivery_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			delivery_address
		};
```

**File:** modules/pallets/relayer/src/accumulate.rs (L317-336)
```rust
impl<T: Config> Pallet<T> {
	/// Decode a proven `RequestReceipts[commitment]` value into the delivering
	/// relayer's bytes. EVM stores the address RLP encoded, substrate stores the
	/// signer bytes or a signature to recover the signer from. Used by both fee
	/// accumulation and the outbound request delivery claim.
	pub fn decode_receipt_relayer(state_id: StateMachine, raw: &[u8]) -> Result<Vec<u8>, Error<T>> {
		match state_id {
			s if crate::is_pharos(&s) =>
				if raw.len() == 32 {
					Ok(Address::from_slice(&raw[12..]).0.to_vec())
				} else {
					Err(Error::<T>::ProofValidationError)
				},
			s if s.is_evm() => {
				use alloy_rlp::Decodable;
				Ok(Address::decode(&mut &*raw)
					.map_err(|_| Error::<T>::ProofValidationError)?
					.0
					.to_vec())
			},
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L81-116)
```rust
	pub fn withdraw(withdrawal_data: WithdrawalInputData) -> DispatchResult {
		let address = match &withdrawal_data.signature {
			Signature::Evm { address, .. } => address.clone(),
			Signature::Sr25519 { public_key, .. } => public_key.clone(),
			Signature::Ed25519 { public_key, .. } => public_key.clone(),
		};

		let nonce = Nonce::<T>::get(address.clone(), withdrawal_data.dest_chain);
		let msg = message(nonce, withdrawal_data.dest_chain, withdrawal_data.beneficiary.clone());

		match &withdrawal_data.signature {
			Signature::Evm { address, .. } => {
				let eth_address = withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
				if &eth_address != address {
					Err(Error::<T>::InvalidPublicKey)?
				}
			},
			Signature::Sr25519 { .. } => {
				// Verify signature with public key provided in signature enum
				withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
			},
			Signature::Ed25519 { .. } => {
				// Verify signature with public key provided in signature enum
				withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
			},
		};
		let available_amount = Fees::<T>::get(withdrawal_data.dest_chain, address.clone());
```
