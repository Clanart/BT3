## Analysis

**Core broken invariant in the seed report:** `Project.raiseDispute()` only authenticates EOA signatures and has no path to also accept `approveHash`-authorized commitments, so any participant identity that is a contract (not an EOA capable of signing) can never invoke a privileged, signature-gated action — a structural authentication gap, not a malicious-actor scenario.

**Local analog:** In Hyperbridge's `pallet-ismp-relayer`, the exact same structural gap exists in the relayer fee flows. A relayer's identity recorded on-chain (in `RequestReceipts`) is simply whatever EVM address (or substrate account) delivered the message — this can legitimately be a smart-contract wallet (e.g. a Safe multisig used by a relayer operator, exactly the "contract as an actor" scenario from the seed report). But every fee-related action gated by `crypto_utils::verification::Signature` only supports three EOA-style signature schemes: [1](#0-0) 

This enum is the sole authentication primitive for:
- `Pallet::withdraw` (fee withdrawal) — the address is derived straight from the `Signature` variant and matched only via `signature.verify(...)`: [2](#0-1) 
- The beneficiary redirect in `accumulate_fees` — same three-variant match, no contract-signature branch: [3](#0-2) 
- `OutboundRequestDeliveryClaim` reward payout — `recovered == delivered_by` compares a raw recovered EOA/pubkey against the receipt-proven address: [4](#0-3) 

None of these three call sites have an EIP-1271 (`isValidSignature`) fallback or an `approveHash`-style mechanism, even though the codebase elsewhere (`SolverAccount.sol`) demonstrates awareness of ERC-1271/contract-account signing for other actors: [5](#0-4) 

### Title
Relayer fee withdrawal, beneficiary redirect, and outbound-request reward claim only accept EOA signatures — funds accrued to a contract-wallet relayer identity become permanently unclaimable - (File: modules/pallets/relayer/src/withdrawal.rs)

### Summary
`pallet-ismp-relayer` records the delivering relayer's identity from raw destination-chain receipt data (an EVM address or substrate account bytes), which can be a smart-contract wallet. However, every extrinsic that lets that identity move the accrued funds — `withdraw_fees`, the beneficiary-redirect branch of `accumulate_fees`, and `claim_outbound_request_delivery_reward` — authenticates exclusively via `crypto_utils::verification::Signature::{Evm, Sr25519, Ed25519}`, all of which require a raw ECDSA/sr25519/ed25519 private-key signature. There is no ERC-1271 (`isValidSignature`) or `approveHash`-style contract-authorization path.

### Finding Description
`Pallet::withdraw` derives the withdrawing `address` directly from the `Signature` enum and requires a raw signature that recovers to that same address: [2](#0-1) . The same pattern recurs in the beneficiary-redirect verification inside `accumulate_fees`: [3](#0-2) , and again in the outbound-request delivery claim, where the recovered signer must equal the raw bytes proven in the destination receipt slot: [4](#0-3) .

None of these paths call out to the destination chain (or any oracle) to validate a contract-based authorization (e.g., ERC-1271 `isValidSignature`, or a Safe-style `approveHash`). If the address stored as the "delivering relayer" (in `RequestReceipts`, decoded via `decode_receipt_relayer`: [6](#0-5) ) is a smart-contract address with no private key — a legitimate configuration for relayer operators who use multisigs for operational security — none of the three fund-movement extrinsics can ever be satisfied, since they all require producing a raw ECDSA/sr25519/ed25519 signature that recovers to that exact address.

### Impact Explanation
Fees that accumulate to a contract-identified relayer in `Fees::<T>` are permanently locked: there is no code path in this pallet capable of authenticating a contract-owned identity to withdraw, redirect, or claim them. This is a genuine, unconditional loss/lock of funds reachable purely from the protocol's own design (any relayer choosing — or forced by receipt data — to be identified by a contract address), not from a malicious peer, relayer, or admin.

### Likelihood Explanation
Any relayer operator using a smart-contract wallet (multisig, account-abstraction wallet, etc.) as their delivery/reward-collecting identity on an EVM destination — a common operational-security practice — will have their address recorded as the "delivering relayer" in `RequestReceipts`, and will subsequently be unable to withdraw the fee/reward via `withdraw_fees`, redirect via `accumulate_fees`'s beneficiary path, or claim via `claim_outbound_request_delivery_reward`. No attacker action is required; it is a deterministic capability gap triggered by normal relayer operation.

### Recommendation
Extend `crypto_utils::verification::Signature` (or the individual verification call sites in `withdrawal.rs`, `accumulate.rs`, and `outbound_request.rs`) with a contract-authorization variant that, for EVM destinations, validates via ERC-1271 `isValidSignature` (through an off-chain-provided proof/oracle acceptable to the pallet's trust model) or an on-chain `approveHash`-equivalent commitment scheme, mirroring how `SolverAccount.sol` already supports ERC-1271 for other Hyperbridge actors.

### Proof of Concept
1. A relayer delivers a request/response on an EVM destination chain using a Safe multisig contract as its `msg.sender`; the destination `RequestReceipts[commitment]` slot stores this contract address.
2. `pallet-ismp-relayer::accumulate_fees` (or the outbound-request claim) accepts the state proof and credits `Fees::<T>::get(state_machine, contract_address)` with the earned reward — this step succeeds because it only checks the proof, not the signer's key type.
3. The relayer operator now attempts `withdraw_fees` (or a beneficiary redirect, or `claim_outbound_request_delivery_reward`) supplying a `Signature`. Because the Safe has no private key, no `Signature::Evm/Sr25519/Ed25519` variant can be produced that recovers to the contract's address; `signature.verify(...)` (`modules/utils/crypto/src/verification.rs:35-72`) will always fail or recover to an unrelated EOA.
4. The accrued balance in `Fees::<T>` for that contract address can never be moved out of the pallet — permanent fund lock, directly analogous to the seed report's "contract-based actor cannot invoke the protected action."

### Citations

**File:** modules/utils/crypto/src/verification.rs (L20-30)
```rust
#[derive(
	Debug, Clone, Encode, Decode, DecodeWithMemTracking, scale_info::TypeInfo, PartialEq, Eq,
)]
pub enum Signature {
	/// An Evm Address and signature
	Evm { address: Vec<u8>, signature: Vec<u8> },
	/// An Sr25519 public key and signature
	Sr25519 { public_key: Vec<u8>, signature: Vec<u8> },
	/// An Ed25519 public key and signature
	Ed25519 { public_key: Vec<u8>, signature: Vec<u8> },
}
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L82-115)
```rust
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
```

**File:** modules/pallets/relayer/src/accumulate.rs (L107-126)
```rust
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
```

**File:** modules/pallets/relayer/src/accumulate.rs (L317-351)
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
			s if s.is_substrate() => {
				use codec::Decode;
				let bytes =
					<Vec<u8>>::decode(&mut &*raw).map_err(|_| Error::<T>::ProofValidationError)?;
				Ok(if bytes.len() > 32 {
					Signature::decode(&mut &*bytes)
						.map_err(|_| Error::<T>::SignatureDecodingError)?
						.signer()
				} else {
					bytes
				})
			},
			_ => Err(Error::<T>::MismatchedStateMachine),
		}
	}
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L169-173)
```rust
		let delivered_by = Self::decode_receipt_relayer(destination, &raw)?;

		let msg = outbound_request_delivery_message(commitment, destination, payee);
		let recovered = signature.verify(&msg, None).map_err(|_| Error::<T>::InvalidSignature)?;
		ensure!(recovered == delivered_by, Error::<T>::OutboundRequestSignerMismatch);
```

**File:** evm/src/apps/intentsv2/SolverAccount.sol (L179-189)
```text
    /**
     * @notice ERC-1271 signature validation for EIP-7702 delegated accounts.
     * @dev Required so that protocols using OpenZeppelin's SignatureChecker (e.g. USDC's
     *      EIP-2612 permit) can verify signatures from this account. Under EIP-7702 the
     *      account has code, so SignatureChecker takes the ERC-1271 path instead of
     *      ecrecover. Delegates to {_rawSignatureValidation} which performs ECDSA recovery
     *      and checks that the recovered address equals address(this) (the delegating EOA).
     */
    function isValidSignature(bytes32 hash, bytes calldata signature) external view override returns (bytes4) {
        return _rawSignatureValidation(hash, signature) ? bytes4(0x1626ba7e) : bytes4(0xffffffff);
    }
```
