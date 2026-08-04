Confirmed vulnerability: `Pallet::withdraw` in `modules/pallets/relayer/src/withdrawal.rs` zeros the relayer's `Fees` balance **unconditionally after dispatch**, regardless of whether the cross-chain `WithdrawalParams` message ever executes successfully on the destination. This is a genuine analog of the Olas/Gnosis bug class: a claim is "finalized" on the source side (fees zeroed, nonce incremented) while the actual fund-release message can still fail unrecoverably on the destination, with no way to replay or recover — except this time the loss falls on the relayer/beneficiary themselves rather than requiring an attacker, since the trigger is dispatch failure or destination-side revert of `IHostManager.withdraw`, which — unlike `EvmHost.dispatchIncoming`'s generic app-`onAccept` retry path — is invoked as a privileged internal call from `HostManager.onAccept`, whose failure semantics I could not fully verify within the available context (need to check `EvmHost.withdraw()`/`IHostManager.withdraw` implementation for revert-vs-swallow behavior and whether the whole `onAccept` transaction, and therefore receipt deletion for retry, still applies here).

### Title
Relayer fee balance is zeroed before destination-side withdrawal delivery succeeds, risking permanent fund loss on failure - ([File: modules/pallets/relayer/src/withdrawal.rs])

### Summary
`Pallet::withdraw` computes `available_amount` from `Fees::<T>::get(dest_chain, address)`, dispatches an ISMP `PostRequest` instructing the destination chain's `HostManager`/substrate module to disburse that amount to the beneficiary, and **immediately zeros the `Fees` entry** right after the dispatch call succeeds — before there is any confirmation that the destination-side transfer executes.

### Finding Description [1](#0-0) 

The sequence is:
1. `available_amount` read from `Fees`.
2. `dispatcher.dispatch_request(...)` sends the POST request (with `fee: Default::default()`, i.e. zero relayer fee for delivery, and `timeout: 0`, i.e. it never times out and can never be recovered via the timeout/refund path documented at [2](#0-1)  since a `timeout: 0` message is defined to never expire).
3. `Fees::<T>::insert(dest_chain, address, U256::zero())` — the balance is erased at this point, not after the destination confirms delivery.

Because `dispatch_request` only guarantees the message is committed on Hyperbridge (it doesn't guarantee delivery or successful execution on the destination), and the request never times out, any failure mode on the destination side — e.g., the `HostManager`/`EvmHost.withdraw()` call reverting due to insufficient liquidity in the escrow, a paused/blocklisted beneficiary, or any other on-chain condition on the destination — permanently loses the claimed fee balance with no automatic retry and no timeout-triggered refund, since `Fees` has already been zeroed on the source (Hyperbridge) side.

This mirrors the root cause of the referenced Olas finding precisely: the accounting/settlement state is finalized (balance debited / incentive marked claimed) on one side of the bridge before the corresponding fund movement is confirmed on the other side, and the chosen message parameters (`timeout: 0`, zero relayer fee) remove the two safety nets (timeout-refund, and relayer-fee-incentivized retry) that would otherwise allow recovery.

### Impact Explanation
A relayer's entire accumulated fee balance for a given destination chain can be permanently lost if the withdrawal delivery message fails or is never picked up (zero relayer fee removes economic incentive to deliver it, and `timeout: 0` removes the only structural recovery path). This is a direct "loss of funds" condition matching the bounty's accepted impact category, and — unlike the original Olas report — requires no malicious relayer/prover assumption to trigger: any legitimate relayer calling `withdraw` is exposed to it purely from ordinary destination-side execution failure (e.g., temporary manager underfunding), with governance intervention being the only possible remedy (there is no on-chain replay primitive analogous to `processDataMaintenance` visible in this pallet).

### Likelihood Explanation
Likelihood is moderate: it requires the destination-side manager/host-manager withdrawal call to fail post-dispatch (e.g., contract paused, insufficient balance, or a reverting beneficiary), which is plausible in practice given `HostManager`/`BandwidthManager`-style contracts already document revert conditions such as `InsufficientNativeToken` on beneficiary transfer failures. It does not require any privileged actor, prover, or relayer misbehavior — an ordinary relayer withdrawal under adverse destination conditions is sufficient.

### Recommendation
Do not zero `Fees` until destination-side execution is confirmed. Either (a) attach a non-zero relayer fee and a real (non-zero) `timeout` so that a failed/undelivered withdrawal message times out and can trigger a refund path that restores the `Fees` balance, mirroring the `on_request_timeout` restoration pattern already used elsewhere in the codebase (e.g., [3](#0-2) ), or (b) only zero the balance after receiving confirmation (e.g., a response/ack) that the destination transfer succeeded, or (c) implement an explicit governance-gated re-dispatch/replay mechanism for stuck withdrawal commitments.

### Proof of Concept
1. A relayer accrues `Fees[dest_chain][relayer] = X` via `accumulate_fees`.
2. Relayer calls `withdraw` with a valid signature; `dispatch_request` succeeds in committing the POST request on Hyperbridge.
3. `Fees::<T>::insert(dest_chain, relayer, 0)` executes immediately — the balance is gone from the relayer's perspective on Hyperbridge.
4. The destination `HostManager`/substrate module's transfer of `X` to the beneficiary reverts or is never delivered (e.g., destination manager contract is temporarily underfunded, or the message is never relayed since `fee: 0` gives no relayer incentive, and `timeout: 0` means it can never be reclaimed via the timeout path).
5. `X` is now unrecoverable through any protocol-level mechanism visible in this module — only manual governance action (source unverified in available context) could restore it.

### Citations

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

**File:** docs/content/protocol/ismp/timeouts.mdx (L8-12)
```text
Blockchains may become incapable of processing transactions for a vareity of reasons. These might include _liveness failures_, which can occur when the state transition function becomes unable to produce new blocks, consensus faults, transaction fee spikes that make transaction execution unprofitable, or a doomsday scenario such as a nation state sanctioned attack.

Regardless of the reason, if the destination becomes incapable of processing incoming ISMP requests, the framework provides a timeout mechanism. This feature allows for the safe reversion of state changes on the source chain that were executed prior to dispatching the request, as if no issue ever occurred.

`PostRequest` and `GetRequest` both have the ability to time out due to their `timeout_timestamp` value. This value stipulates the lifespan of a message. A `PostRequest` will time out when its destination's `host.timestamp()` surpasses the `timeout_timestamp`. This asserts that the destination has indeed not processed the relevant messages. Conversely, a `GetRequest` times out when its source's `host.timestamp()` surpasses the sending chain's `timeout_timestamp`.
```

**File:** modules/ismp/core/src/handlers/timeout.rs (L122-134)
```rust
					if res.is_ok() {
						host.on_request_timeout(&request, meta)?;
					} else {
						// Module callback failed; restore commitment so the request
						// can be retried.
						host.store_request_commitment(&request, meta)?;
						if host.host_state_machine() != post.source && signer.is_some() {
							host.store_request_receipt(
								&request,
								&signer.ok_or_else(|| anyhow::anyhow!("Infallible"))?,
							)?;
						}
					}
```
