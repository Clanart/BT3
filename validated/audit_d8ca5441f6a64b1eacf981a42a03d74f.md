Based on my investigation, the strongest local analog to the "missing derivation/authority check on a trusted account" bug class is in the ISMP withdrawal module's source-authentication logic, not in the intent/token-gateway paths (those already bind commitments, module addresses, and use nonces/receipts correctly).

### Title
`HyperbridgeWithdrawalModule::on_accept` authenticates only the source chain, never the sending module, allowing forged relayer-fee withdrawal messages - (File: `modules/pallets/ismp/src/dispatcher.rs`)

### Summary
The built-in withdrawal handler that pays out relayer fees on destination chains validates that an incoming ISMP `PostRequest` came from the correct **chain** (`request.source == Coprocessor`), but never validates that it came from the correct **module/pallet** on that chain (`request.from`). This mirrors the reported bug class exactly: an account/identity field is accepted and trusted for a fund-moving operation without validating it was derived/authorized through the expected path (seeds/bump in the original report; module id here).

### Finding Description
`HyperbridgeWithdrawalModule::on_accept` is registered as the handler for module id `HYPERBRIDGE_MODULE_ID` via `IsmpHostRouter::module_for_id`: [1](#0-0) 

When a request with `to == HYPERBRIDGE_MODULE_ID` arrives, the only check performed is on the chain identity of the source: [2](#0-1) 

Note that `request.from` (the identifier of the pallet/module on the coprocessor chain that actually dispatched the message) is never inspected. The decoded `Message::WithdrawRelayerFees { account, amount }` is trusted at face value and directly triggers a `Currency::transfer` from `RELAYER_FEE_ACCOUNT` to `account` for `amount`: [3](#0-2) 

Compare this with the legitimate dispatch path (`modules/pallets/relayer/src/withdrawal.rs`), where an actual relayer fee withdrawal is only ever initiated after (a) a signature check tying the request to a specific relayer key, and (b) a decrement of that relayer's `Fees` balance: [4](#0-3) 

The dispatcher-level `IsmpDispatcher::dispatch_request` is a generic, low-level primitive — any pallet on the coprocessor runtime (and, per the documented pattern in `docs/content/developers/polkadot/dispatching.mdx`, potentially any signed extrinsic wired to it) can construct a `DispatchPost` with an arbitrary `to` and `body`, choosing its own `from` value: [5](#0-4) 

Because `on_accept` in the withdrawal module never checks `from`, **any** module capable of dispatching a `PostRequest` from the coprocessor chain toward `HYPERBRIDGE_MODULE_ID` on a destination chain can impersonate the relayer-fee pallet and mint an arbitrary `WithdrawRelayerFees` payout to any beneficiary account, without any of the signature/nonce/balance-decrement checks that the legitimate `withdraw` flow enforces.

### Impact Explanation
If an unprivileged actor (or any pallet not intended to control relayer payouts) can get a `PostRequest` dispatched from the coprocessor chain with `to = HYPERBRIDGE_MODULE_ID` and a `body` that decodes as `Message::WithdrawRelayerFees`, funds are transferred straight out of `RELAYER_FEE_ACCOUNT` on the destination chain to an attacker-chosen account for an attacker-chosen amount — a direct, unauthorized fund drain, with no cap tied to actually-accrued fees (the real accrual/decrement bookkeping in `Fees::<T>` on the relayer pallet is entirely bypassed).

### Likelihood Explanation
The check as coded is objectively incomplete: it validates a coarse-grained field (chain id) and omits the fine-grained field (`from`) needed to bind the message to the one pallet that is supposed to be authorized to issue these payouts. Whether this is reachable by a fully unprivileged, un-trusted extrinsic in the current production coprocessor runtime configuration could not be confirmed from the indexed code (the runtime's own extrinsic-to-dispatch wiring for the live Hyperbridge coprocessor was not visible in the indexed files, only the example/demo pallets which show the general capability of the dispatcher API). This is the main open uncertainty; regardless, the `on_accept` guard itself is missing the sender-identity check that the analog bug class requires, and it should not rely solely on `to`-routing plus source-chain check for a fund-moving instruction.

### Recommendation
In `HyperbridgeWithdrawalModule::on_accept`, additionally validate `request.from` against a configured, single trusted module id (e.g. the coprocessor-side relayer/treasury pallet id) before decoding and acting on `Message::WithdrawRelayerFees`, exactly as `pallet_token_gateway::is_token_gateway` binds messages to specific registered contract addresses per state machine. Do not trust chain-level source alone for a call that moves funds out of `RELAYER_FEE_ACCOUNT`.

### Proof of Concept
1. On the coprocessor chain, any pallet/extrinsic with access to `T::IsmpDispatcher` constructs:
   ```rust
   let post = DispatchPost {
       dest: <victim_destination_chain>,
       from: <arbitrary_bytes>,          // never checked on destination
       to: HYPERBRIDGE_MODULE_ID.to_vec(),
       timeout: 0,
       body: Message::<AccountId, Balance>::WithdrawRelayerFees(
           WithdrawalRequest { amount: <arbitrary_amount>, account: <attacker_account> }
       ).encode(),
   };
   dispatcher.dispatch_request(DispatchRequest::Post(post), fee_metadata)?;
   ```
2. Once delivered and proven on the destination chain (source chain id correctly matches `Coprocessor`, which is legitimate/provable), `HyperbridgeWithdrawalModule::on_accept` only checks `request.source == Coprocessor` — true here — and proceeds to decode the body and transfer `amount` from `RELAYER_FEE_ACCOUNT` to `attacker_account`, at `modules/pallets/ismp/src/dispatcher.rs:200-213`, with no verification that `from` corresponds to the legitimate relayer-fee withdrawal logic in `modules/pallets/relayer/src/withdrawal.rs`.

### Citations

**File:** modules/pallets/ismp/src/dispatcher.rs (L128-146)
```rust
			DispatchRequest::Post(dispatch_post) => {
				let post = PostRequest {
					source: self.host_state_machine(),
					dest: dispatch_post.dest,
					nonce: self.next_nonce(),
					from: dispatch_post.from,
					to: dispatch_post.to,
					timeout_timestamp: if dispatch_post.timeout == 0 {
						0
					} else {
						<T::TimestampProvider as UnixTime>::now()
							.as_secs()
							.saturating_add(dispatch_post.timeout)
					},
					body: dispatch_post.body,
				};
				Request::Post(post)
			},
		};
```

**File:** modules/pallets/ismp/src/dispatcher.rs (L168-176)
```rust
impl<T: Config> IsmpRouter for IsmpHostRouter<T> {
	fn module_for_id(&self, id: Vec<u8>) -> Result<Box<dyn IsmpModule>, anyhow::Error> {
		if id.as_slice() == HYPERBRIDGE_MODULE_ID {
			return Ok(Box::new(HyperbridgeWithdrawalModule::<T>::default()));
		}

		self.inner.module_for_id(id)
	}
}
```

**File:** modules/pallets/ismp/src/dispatcher.rs (L189-217)
```rust
impl<T: Config> IsmpModule for HyperbridgeWithdrawalModule<T> {
	fn on_accept(&self, request: PostRequest) -> Result<Weight, anyhow::Error> {
		// Only the configured coprocessor may instruct withdrawals.
		let source = request.source;
		if Some(source) != T::Coprocessor::get() {
			Err(IsmpError::Custom(format!("Invalid request source: {source}")))?
		}

		let message = Message::<T::AccountId, T::Balance>::decode(&mut &request.body[..])
			.map_err(|err| IsmpError::Custom(format!("Failed to decode message: {err:?}")))?;

		match message {
			Message::WithdrawRelayerFees(WithdrawalRequest { account, amount }) => {
				T::Currency::transfer(
					&RELAYER_FEE_ACCOUNT.into_account_truncating(),
					&account,
					amount,
					Preservation::Expendable,
				)
				.map_err(|err| {
					IsmpError::Custom(format!("Error withdrawing protocol fees: {err:?}"))
				})?;

				Pallet::<T>::deposit_event(Event::<T>::RelayerFeeWithdrawn { amount, account });
			},
		}

		Ok(<T as frame_system::Config>::DbWeight::get().reads_writes(0, 0))
	}
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L81-134)
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

		if available_amount <
			Self::min_withdrawal_amount(withdrawal_data.dest_chain)
				.unwrap_or(MinWithdrawal::get())
		{
			Err(Error::<T>::NotEnoughBalance)?
		}

		let dispatcher = <T as Config>::IsmpHost::default();

		Nonce::<T>::try_mutate(address.clone(), withdrawal_data.dest_chain, |value| {
			*value += 1;
			Ok::<(), ()>(())
		})
		.map_err(|_| Error::<T>::ErrorCompletingCall)?;

		let beneficiary_address = withdrawal_data.beneficiary.clone().unwrap_or(address.clone());
		let (to, body) = match withdrawal_data.dest_chain {
```
