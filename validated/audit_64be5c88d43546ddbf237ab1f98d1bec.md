### Title
Relayer fee balance is zeroed before destination-chain payout is confirmed, with no timeout/rollback path if delivery never lands - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`pallet-ismp-relayer`'s `withdraw()` optimistically deletes a relayer's accrued `Fees` entry the moment it dispatches the payout request, before the destination chain has actually transferred any tokens. The dispatched request is sent with `timeout: 0` (never times out) and with a zeroed `FeeMetadata { payer: 0, fee: 0 }`, which strips it out of pallet-ismp's normal timeout-refund pipeline. If the destination-side payout never completes — for example because the destination `HostManager`/`EvmHost` doesn't hold enough fee tokens (a state the codebase's own test explicitly exercises and expects to revert) — the relayer's accrued balance is gone from Hyperbridge's ledger with no mechanism to restore it.

### Finding Description
`Pallet::withdraw` in [1](#0-0)  reads `available_amount` from `Fees`, verifies the relayer's signature, then unconditionally dispatches an ISMP `DispatchPost` carrying a `WithdrawalParams`/`WithdrawRelayerFees` payload to the destination chain's `HostManager` (EVM) or `HYPERBRIDGE_MODULE_ID` (substrate): [2](#0-1) 

Two properties of this dispatch break the accounting invariant:

1. **`Fees` is zeroed unconditionally, synchronously with dispatch, not with delivery.** `Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero())` runs right after `dispatch_request(...)` returns `Ok`, i.e. as soon as the *request* is accepted into the outbound queue — not after the destination chain has actually executed the transfer. The module's own doc comment states this plainly: "The on-chain effect is just dispatching the message; the destination chain settles the payout when the ISMP request is delivered there," and "The `Fees` entry is zeroed so the same balance cannot be withdrawn twice" [3](#0-2) .

2. **The dispatch is `timeout: 0` (never times out) with zero fee/payer**, which removes it from the standard ISMP refund path. Normally, `on_request_timeout` in pallet-ismp's host implementation refunds `leaf_meta.fee.payer` the `leaf_meta.fee.fee` amount when a request times out [4](#0-3) . But the withdrawal dispatch here uses `FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() }` [5](#0-4) , so even if the request could time out, there is nothing to refund and no link back to the relayer's zeroed `Fees` entry.

Meanwhile, destination-side execution is not guaranteed to succeed: `HostManager.onAccept` forwards the `Withdraw` action straight to `IHostManager(_params.host).withdraw(withdrawParams)` [6](#0-5) , and the repo's own test demonstrates this reverts when the host lacks sufficient fee-token balance: `test_host_manager_insufficient_balance` mints nothing to the host and asserts the `onAccept` call reverts [7](#0-6) , contrasted with `test_host_manager_withdraw`, which only succeeds once the host has been pre-funded [8](#0-7) .

So the sequence a relayer (or Hyperbridge treasury operations in general) can hit through entirely normal, non-malicious use is:
- Relayer accrues fees via `accumulate_fees` → `Fees[chain][relayer] += total_fee` [9](#0-8) .
- Relayer calls `withdraw()`. `Fees` is zeroed and a no-timeout, zero-fee ISMP POST is dispatched to the destination `HostManager`.
- If the destination host's fee-token balance is insufficient at delivery time (revenue not yet swept in, treasury drained by governance `dispatch_withdraw`, oracle/router failure mid-swap-funded host, etc.), the destination `onAccept` reverts and the payout never lands.
- Because `timeout: 0`, there is no timeout window after which the relayer (or anyone) can trigger `on_request_timeout` to get a refund — and even if there were, the zero `FeeMetadata` means no refund would be computed since `payer`/`fee` are zero.
- `Fees` on Hyperbridge is already zero. The relayer has no path within the protocol to reclaim the "accrued interest/fees" it was promised.

This is structurally identical to the rToken analog: the ledger (`Fees` mapping / rToken balance) represents a promise of value ("deposit + accrued interest" / "accrued relayer fee"), but the actual liquidity backing that promise on the paying side (destination `HostManager` balance / rToken's underlying asset pool) is not guaranteed to be sufficient at redemption time, and once the ledger entry is consumed there is no invariant-preserving fallback.

### Impact Explanation
This directly causes loss of relayer funds: a relayer's legitimately earned, protocol-tracked fee balance can be permanently destroyed (`Fees` zeroed) without the relayer ever receiving payment, if the destination chain's `HostManager`/host contract balance is insufficient at delivery time and the request can never time out to trigger any restorative accounting. This is a genuine fund-loss bug in the core relayer incentive/reward accounting, not contingent on a malicious relayer, prover, or governance actor — any relayer performing a routine, permissionless `withdraw()` call can be affected purely by normal liquidity timing on the destination host.

### Likelihood Explanation
Likelihood is non-trivial: destination-host fee-token balances are managed asynchronously (accrued from various dispatch fees, occasionally swept out by governance via `dispatch_withdraw` as documented in the bandwidth governance withdrawal flow [10](#0-9) ), so a race between a relayer's withdrawal request landing and the host being temporarily under-funded is plausible in production operation, and the repo's own dedicated `test_host_manager_insufficient_balance` test confirms this failure mode is expected/reachable rather than purely theoretical.

### Recommendation
Do not zero `Fees` until destination-side delivery is confirmed (e.g., only clear the balance after processing a delivery acknowledgment/response back to Hyperbridge, or keep a pending/escrow state that can be reconciled). Alternatively, give the withdrawal dispatch a real timeout and a non-zero `FeeMetadata` tied to a Hyperbridge-owned escrow account, so that on-timeout (or on explicit destination-side revert reporting) the `Fees` entry can be restored via the existing `on_request_timeout` refund pipeline rather than being unconditionally and irreversibly cleared at dispatch time.

### Proof of Concept
Not independently executable from the indexed context (no full local Substrate/EVM integration test harness for `pallet-ismp-relayer` + `HostManager` interplay was retrievable), but the failure path is demonstrable by combining two existing repo tests/behaviors:
1. Accrue fees for a relayer via `accumulate_fees`, then call `Pallet::<T>::withdraw(...)` — observe `Fees::<T>::insert(dest_chain, relayer, U256::zero())` executes immediately per [11](#0-10) , before any destination confirmation.
2. Simultaneously run `test_host_manager_insufficient_balance` [7](#0-6)  to show the destination `onAccept` call for the exact same `WithdrawalParams` shape reverts when the host lacks fee tokens — confirming the payout never lands while the source-side `Fees` ledger entry has already been destroyed with no timeout (`timeout: 0`) or fee-refund metadata to recover it.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L16-30)
```rust
//! Relayer fee withdrawal.
//!
//! Once fees have been accumulated into [`crate::pallet::Fees`] by
//! [`crate::accumulate`], relayers withdraw them via [`Pallet::withdraw`].
//! The flow:
//!
//! 1. The relayer signs a `(nonce, dest_chain, beneficiary?)` payload with their per-chain key (EVM
//!    secp256k1 / sr25519 / ed25519).
//! 2. The pallet verifies the signature, increments the per-relayer nonce, and dispatches an ISMP
//!    POST request to the destination's host manager (EVM) or `HYPERBRIDGE_MODULE_ID` (substrate)
//!    instructing it to disburse `available_amount` of the fee token to the beneficiary.
//! 3. The `Fees` entry is zeroed so the same balance cannot be withdrawn twice.
//!
//! The on-chain effect is just dispatching the message; the destination chain settles the
//! payout when the ISMP request is delivered there.
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L81-123)
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
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L161-187)
```rust
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

		Ok(())
	}
```

**File:** modules/pallets/ismp/src/host.rs (L322-334)
```rust
	fn on_request_timeout(&self, _req: &Request, meta: Vec<u8>) -> Result<(), Error> {
		let leaf_meta = RequestMetadata::<T>::decode(&mut &*meta)
			.map_err(|_| Error::Custom("Failed to decode leaf metadata".to_string()))?;
		if leaf_meta.fee.fee > Zero::zero() {
			T::Currency::transfer(
				&RELAYER_FEE_ACCOUNT.into_account_truncating(),
				&leaf_meta.fee.payer,
				leaf_meta.fee.fee,
				Preservation::Expendable,
			)
			.map_err(|err| Error::Custom(format!("Failed to refund relayer fee: {err:?}")))?;
		}
		Ok(())
```

**File:** evm/src/core/HostManager.sol (L95-109)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
        PostRequest calldata request = incoming.request;
        // Only the Hyperbridge parachain can send requests to this module.
        if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();

        OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
        if (action == OnAcceptActions.Withdraw) {
            // This is where governance & relayers can withdraw their revenue.
            WithdrawParams memory withdrawParams = abi.decode(request.body[1:], (WithdrawParams));
            IHostManager(_params.host).withdraw(withdrawParams);
        } else if (action == OnAcceptActions.SetHostParam) {
            HostParams memory hostParams = abi.decode(request.body[1:], (HostParams));
            IHostManager(_params.host).updateHostParams(hostParams);
        }
    }
```

**File:** evm/tests/rust/src/tests/host_manager.rs (L72-109)
```rust
#[test]
fn test_host_manager_withdraw() {
	let mut env = TestEnv::new();
	let manager = host_manager_of(&mut env);

	// Mint 1000e18 fee tokens to the host
	let amount_to_mint = U256::from(1000u128) * U256::from(10u128.pow(18));
	env.call(env.fee_token, mintCall { to: env.host, amount: amount_to_mint }.abi_encode());
	assert_eq!(host_balance(&mut env), amount_to_mint);

	// Build a withdraw request (body = [0] + abi.encode(WithdrawParams)).
	// Withdraw the fee token (non-zero `token`) — the zero address would be
	// the native-ETH path which this test isn't exercising.
	let params = WithdrawalParams {
		beneficiary_address: H160::random().as_bytes().to_vec(),
		amount: SubstrateU256::from(500_000_000_000_000_000_000u128),
		token: H160::from_slice(env.fee_token.as_slice()),
	};

	let post = router::PostRequest {
		source: StateMachine::Kusama(2000),
		dest: StateMachine::Evm(1),
		nonce: 0,
		from: env.sender.as_slice().to_vec(),
		to: vec![],
		timeout_timestamp: 100,
		body: params.abi_encode().expect("20-byte beneficiary"),
	};
	let evm_request: EvmPostRequest = post.into();

	// HostManager.onAccept is `restrict(_params.host)` — must call AS the host
	let host_addr = env.host;
	let calldata = onaccept_calldata(evm_request, env.sender);
	env.call_as(host_addr, manager, calldata);

	let withdraw_amount = U256::from(500u128) * U256::from(10u128.pow(18));
	assert_eq!(host_balance(&mut env), amount_to_mint - withdraw_amount);
}
```

**File:** evm/tests/rust/src/tests/host_manager.rs (L143-172)
```rust
#[test]
fn test_host_manager_insufficient_balance() {
	let mut env = TestEnv::new();
	let manager = host_manager_of(&mut env);

	// Host has no fee tokens; withdraw attempt should fail on SafeERC20 transfer
	let params = WithdrawalParams {
		beneficiary_address: H160::random().as_bytes().to_vec(),
		amount: SubstrateU256::from(500_000_000_000_000_000_000u128),
		token: H160::from_slice(env.fee_token.as_slice()),
	};

	let post = router::PostRequest {
		source: StateMachine::Kusama(2000),
		dest: StateMachine::Evm(1),
		nonce: 0,
		from: env.sender.as_slice().to_vec(),
		to: vec![],
		timeout_timestamp: 100,
		body: params.abi_encode().expect("20-byte beneficiary"),
	};
	let evm_request: EvmPostRequest = post.into();

	let host_addr = env.host;
	let calldata = onaccept_calldata(evm_request, env.sender);
	let err = env
		.call_as_may_revert(host_addr, manager, calldata)
		.expect_err("expected revert");
	assert!(!err.is_empty(), "expected non-empty revert data");
}
```

**File:** modules/pallets/relayer/src/accumulate.rs (L128-147)
```rust
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

**File:** docs/content/developers/evm/bandwidth/governance.mdx (L34-55)
```text
## Treasury Withdrawals

The fee tokens collected by `BandwidthManager.purchase()` accumulate in the manager contract. Governance drains them with `dispatch_withdraw`:

```rust
BandwidthPallet::dispatch_withdraw(
    RawOrigin::Root.into(),
    StateMachine::Evm(8453),
    fee_token_address,              // ERC-20 to withdraw. Use H160::zero() for native ETH.
    treasury_address,               // Beneficiary.
    U256::from(1_000_000_000u128),  // Amount, in fee-token decimals.
);
```

The pallet dispatches `[ACTION_WITHDRAW, abi.encode(Withdrawal)]` where `Withdrawal { token, beneficiary, amount }`. The manager's `onAccept` handler:

- If `token != address(0)`: `IERC20(token).safeTransfer(beneficiary, amount)`.
- If `token == address(0)`: `beneficiary.call{value: amount}("")` — reverts with `InsufficientNativeToken` if the call fails (insufficient balance, beneficiary rejects ETH, etc.).

The `token` field is named explicitly because the host occasionally swaps fee tokens (e.g. accepting native ETH on dispatch and routing through Uniswap). Stale balances of an old fee token still sit in the manager and are recoverable by specifying the old token's address.

Emits `Withdrawn(token, beneficiary, amount)` on the manager and `WithdrawalDispatched { target, token, beneficiary, amount, commitment }` on the pallet.
```
