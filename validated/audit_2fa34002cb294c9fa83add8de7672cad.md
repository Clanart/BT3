### Title
Missing zero-address validation on relayer fee withdrawal beneficiary permanently burns accumulated fees - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`Pallet::withdraw` in the relayer pallet lets a relayer specify an arbitrary `beneficiary: Option<Vec<u8>>` for their accumulated cross-chain fee payout. Before dispatching, the pallet zeroes the relayer's `Fees` balance [1](#0-0) , but neither this pallet nor the `WithdrawalParams` ABI conversion used for EVM destinations rejects a beneficiary that decodes to the EVM zero address. The conversion only checks that the byte length is exactly 20, not that the value is non-zero [2](#0-1) . This is the same missing zero-address guard class as the reported `setGov()` issue: once the state is committed/dispatched, there is no way to recover.

### Finding Description
The withdrawal flow works as follows:
1. `Pallet::withdraw` takes the caller-supplied `beneficiary_address` (defaulting to the relayer's own address only if `None`), builds `WithdrawalParams`, and immediately zeroes `Fees::<T>::insert(..., U256::zero())` before/around dispatch [3](#0-2) .
2. `WithdrawalParams::abi_encode` -> `TryFrom<&WithdrawalParams> for WithdrawParamsAbi` only validates `beneficiary_address.len() == 20`; an all-zero 20-byte value passes this check and produces `H160::zero()` [2](#0-1) .
3. The resulting `WithdrawParams{ beneficiary: address(0), amount, token }` payload is dispatched as an ISMP POST to the destination `host_manager` (the `HostManager`/`EvmHost` withdraw handler), which transfers `amount` of `token` to `beneficiary` with no additional zero-address check on the EVM side (the analogous `BandwidthManager.onAccept` `Withdraw` action shows the same pattern — it transfers to `w.beneficiary` without validating it is non-zero) [4](#0-3) .
4. Because the relayer's `Fees` entry has already been zeroed at dispatch time (irreversible on the source chain) and the destination-side transfer to `address(0)` either reverts (locking funds in the manager contract, unclaimable) or succeeds (burning the tokens permanently), the relayer's entire accumulated fee balance for that destination chain is unrecoverably lost — mirroring the "loss of access forever" impact of the original `setGov()` zero-address bug, just applied to a fund-custody path instead of an access-control path.

### Impact Explanation
This is a fund-loss bug reachable through a public/unprivileged bridge entrypoint (`relayer::withdraw`), not through an admin, governance, or malicious-relayer/prover assumption — any relayer redeeming legitimately earned fees can trigger it with a single malformed/zero beneficiary value (e.g. client-side bug, copy-paste error, or truncated address input). The `Fees` balance is debited on the source chain regardless of whether the destination-side transfer to the zero address succeeds, so the loss is permanent and not recoverable through governance intervention on the source chain.

### Likelihood Explanation
Medium likelihood: nothing prevents a caller of `withdraw` (or any other consumer that constructs a `WithdrawalParams`, including the host-executive pallet's own `withdraw` call) from supplying an all-zero 20-byte beneficiary; the length-only check in `TryFrom<&WithdrawalParams>` explicitly allows it [2](#0-1) . There is no UI/runtime-level guard forcing a valid non-zero beneficiary before the fee balance is zeroed and the message is dispatched.

### Recommendation
Add an explicit non-zero check on `beneficiary_address` (and equivalently on `Withdrawal.beneficiary`/`WithdrawParams.beneficiary` on the Solidity side) at the earliest validation point — inside `TryFrom<&WithdrawalParams> for WithdrawParamsAbi` in `evm/rust/src/host_params.rs`, and defensively inside the EVM-side withdraw handlers (`EvmHost`/`HostManager`, `BandwidthManager.onAccept`) — rejecting `H160::zero()`/`address(0)` before any balance is debited or funds transferred.

### Proof of Concept
1. A relayer accrues fees for `dest_chain` via `accumulate.rs`.
2. Relayer calls `Pallet::withdraw` with a valid signature but `beneficiary: Some(vec![0u8; 20])`.
3. `TryFrom<&WithdrawalParams>` accepts the input (length == 20) and produces `beneficiary = H160::zero()` [5](#0-4) .
4. `Fees::<T>::insert(dest_chain, relayer, U256::zero())` executes, permanently zeroing the relayer's claim [6](#0-5) .
5. The dispatched ISMP message reaches the destination `host_manager`, which sends `amount` of `token` to `address(0)` — funds are burned/locked with no recovery path.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L133-184)
```rust
		let beneficiary_address = withdrawal_data.beneficiary.clone().unwrap_or(address.clone());
		let (to, body) = match withdrawal_data.dest_chain {
			s if s.is_substrate() => (
				HYPERBRIDGE_MODULE_ID.to_vec(),
				Message::WithdrawRelayerFees(WithdrawalRequest {
					amount: available_amount.low_u128(),
					account: AccountId32::try_from(&beneficiary_address[..])
						.map_err(|_| Error::<T>::InvalidPublicKey)?,
				})
				.encode(),
			),
			_ => {
				let HostParam::EvmHostParam(params) =
					HostParams::<T>::get(withdrawal_data.dest_chain)
						.ok_or_else(|| Error::<T>::MissingMangerAddress)?;

				let body = WithdrawalParams {
					beneficiary_address: beneficiary_address.clone(),
					amount: available_amount.into(),
					token: params.fee_token,
				}
				.abi_encode()
				.map_err(|_| Error::<T>::InvalidPublicKey)?;

				(params.host_manager.0.to_vec(), body)
			},
		};

		let post = DispatchPost {
			dest: withdrawal_data.dest_chain,
			from: MODULE_ID.to_vec(),
			to,
			body,
			timeout: 0,
		};

		// Account is not useful in this case
		dispatcher
			.dispatch_request(
				DispatchRequest::Post(post),
				FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() },
			)
			.map_err(|_| Error::<T>::DispatchFailed)?;

		Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());

		Self::deposit_event(Event::<T>::Withdraw {
			address: sp_runtime::BoundedVec::truncate_from(address.clone()),
			beneficiary_address: sp_runtime::BoundedVec::truncate_from(beneficiary_address),
			state_machine: withdrawal_data.dest_chain,
			amount: available_amount,
		});
```

**File:** evm/rust/src/host_params.rs (L242-260)
```rust
impl TryFrom<&WithdrawalParams> for WithdrawParamsAbi {
	type Error = WithdrawalParamsError;

	fn try_from(value: &WithdrawalParams) -> Result<Self, Self::Error> {
		// Reject anything that isn't exactly a 20-byte EVM address.
		if value.beneficiary_address.len() != 20 {
			Err(WithdrawalParamsError::InvalidBeneficiaryAddressLength(
				value.beneficiary_address.len(),
			))?;
		}
		let beneficiary = H160::from_slice(&value.beneficiary_address[..]);

		Ok(WithdrawParamsAbi {
			beneficiary: beneficiary.0.into(),
			amount: alloy_primitives::U256::from_be_bytes(value.amount.to_big_endian()),
			token: value.token.0.into(),
		})
	}
}
```

**File:** evm/src/apps/BandwidthManager.sol (L213-221)
```text
        } else if (action == OnAcceptActions.Withdraw) {
            Withdrawal memory w = abi.decode(request.body[1:], (Withdrawal));
            if (w.token != address(0)) {
                IERC20(w.token).safeTransfer(w.beneficiary, w.amount);
            } else {
                (bool sent,) = w.beneficiary.call{value: w.amount}("");
                if (!sent) revert InsufficientNativeToken();
            }
            emit Withdrawn(w.token, w.beneficiary, w.amount);
```
