### Title
Outbound-request delivery reward is drained by unprivileged, uncapped-count dispatches from an allowlisted `module_id` - ([File: modules/pallets/relayer/src/outbound_request.rs] and [File: modules/pallets/demo/src/lib.rs])

### Summary
`pallet-relayer`'s outbound-request-delivery incentive was designed to pay relayers for delivering *hyperbridge-originated system messages* (host-executive updates, intents-coprocessor responses, token-governor messages) that dispatch with `FeeMetadata{payer:0, fee:0}` and therefore have no economic incentive of their own [1](#0-0) . The reward is keyed *only* by `module_id = request.from`, a governance-set flat amount per module regardless of who triggered the dispatch or how often [2](#0-1) [3](#0-2) . Nothing in the dispatcher or the claim pipeline restricts *how many times* or *by whom* a request from that `module_id` can be produced — the `from` field is simply whatever the dispatching pallet's code hard-codes into `DispatchPost`, and the dispatch call itself is reachable by any signed account [4](#0-3) .

### Finding Description
This is the direct analog of the 0x bug: the protocol intends a specific class of ZRX ("hyperbridge-originated, unpaid system messages") to receive a specific treatment (treasury-funded relayer reward), while a different class ("user-paid messages") is excluded. But the gate that separates the two classes is a single, cheaply-forgeable field (`module_id`/`from`) rather than an actual guarantee that the dispatch is system-initiated and rate-limited.

- `pallet_ismp::Pallet<T>::dispatch_request` builds the `PostRequest` straight from the caller-supplied `DispatchPost.from` with no cross-check against who actually invoked the extrinsic [5](#0-4) .
- `pallet-ismp-demo`'s `dispatch_to_evm` is `ensure_signed`-only (any account) and dispatches `params.count` copies of a `PostRequest` with a **fixed** `from = PALLET_ID.to_bytes()` (`b"ismp-ast"`) and `fee: Default::default()` — i.e., unpaid, in a caller-controlled loop [4](#0-3) .
- `set_outbound_request_delivery_reward(module_id, amount)` is a flat, governance-set per-`module_id` reward, applied to *every* request from that module to *any* destination, with no per-account or per-block cap [6](#0-5) .
- `process_outbound_request_delivery_claim` only checks: commitment hash, `request.source == host`, presence in `RequestCommitments`, not-already-claimed, `module_id` allowlisted (non-zero reward), destination-type match, state-proof validity, and signer attribution [7](#0-6) . None of these checks limit the *volume* of eligible requests a single unprivileged account can manufacture from an allowlisted module.

Governance registered exactly this pattern live on the gargantua testnet: `OutboundRequestDeliveryReward[b"ismp-ast"] = 10 tBRIDGE`, and the module producing that `from` id (`pallet-ismp-demo`) is triggerable by any signed account with an attacker-chosen `count` [8](#0-7) [4](#0-3) .

The same structural weakness generalizes to production modules named in the design (`intents-coprocessor` responses, `token-governor` messages): any pallet that (a) dispatches with `payer=0,fee=0`, (b) has its `module_id` allowlisted with a non-zero reward, and (c) can be triggered repeatedly and cheaply by an ordinary user, turns the treasury reward into an unauthenticated, per-message payout that a user (optionally colluding with or running their own relayer) can trigger at will — the "reduced weight" (unpaid system message) is bypassed by wrapping the trigger in a permissionless call, exactly as the external contract in the 0x report bypassed the delegated-stake weight discount.

### Impact Explanation
Each triggered dispatch from an allowlisted `module_id`, once delivered and claimed, drains a fixed reward from the Hyperbridge treasury (`TreasuryPalletId`, `hb/trsry`) to the delivering relayer's payee account [9](#0-8) . Because the reward is per-request and flat regardless of message content/size, and because dispatch volume from a permissionless module is entirely attacker-controlled (`count` parameter, or repeated calls), an attacker who also runs (or colludes with) a relayer can systematically siphon treasury BRIDGE funds far in excess of the legitimate "keep system messages flowing" use case the incentive was designed for — a direct, repeatable loss of protocol funds to an unintended beneficiary, matching the bounty's "stealing or loss of funds" / "logic attacks" / "unauthorized ... transaction manipulation" categories.

### Likelihood Explanation
The trigger path (`dispatch_to_evm`, `ensure_signed`, attacker-chosen `count`) is exactly what shipped and was exercised as the reference manual test for this feature, meaning it is not a hypothetical construction — it is the real code path governance registered a reward against [10](#0-9) . No relayer, prover, or admin compromise is required: the attacker only needs (1) an ordinary signed account to call the permissionless dispatch extrinsic in a loop, and (2) a relayer (their own or any participant) to deliver and claim, which is the intended, permissionless relay role. The main uncertainty is whether `pallet-ismp-demo` (the "ping" module) is included in the production runtime or is testnet/demo-only — the docs frame it explicitly as a testnet reward-registration vehicle, so this specific module may be excluded from a mainnet allowlist. The underlying design flaw, however — an unrate-limited, `module_id`-only allowlist gate for a flat treasury payout — applies to any production module (e.g. intents-coprocessor, token-governor) that both dispatches unpaid system requests and exposes a user-triggerable code path capable of generating many such dispatches; confirming whether such a path exists in those pallets would require deeper review of `modules/pallets/intents-coprocessor/src/lib.rs` and `modules/pallets/token-governor/src/impls.rs`, which the available index did not fully expose in this pass.

### Recommendation
- Do not key the outbound-request-delivery reward solely on `module_id`; also bound the *number* or *rate* of reward-eligible dispatches per module per epoch (e.g., a per-block/per-era cap on `OutboundRequestDeliveryReward` payouts per `module_id`), or size the reward to genuine "governance/system necessity" rather than a flat per-commitment amount.
- Restrict which extrinsics are allowed to set `from` to an allowlisted system `module_id` — e.g., require that only privileged/root-origin dispatch paths (not `ensure_signed` user calls) can produce requests whose `from` matches a rewarded `module_id`.
- If `pallet-ismp-demo`/ping-style modules are ever allowlisted in a live (non-test) runtime, remove them from the reward map or gate their dispatch behind an origin check.
- Audit `intents-coprocessor` and `token-governor` dispatch sites for any user-reachable, repeatable call sequence that produces unpaid, `module_id`-eligible outbound requests, and add per-user/per-time throttling before those modules are added to `OutboundRequestDeliveryReward`.

### Proof of Concept
1. Governance sets `Relayer::set_outbound_request_delivery_reward(module_id = b"ismp-ast", amount = REWARD)` (as already done on gargantua) [11](#0-10) .
2. Any signed account (no special privilege) calls `IsmpDemo::dispatch_to_evm(EvmParams { module, destination, timeout, count: N })` with a large `N`, producing `N` `PostRequest`s each with `from = b"ismp-ast"`, `fee = 0`, `payer = 0` [4](#0-3) .
3. A relayer (potentially the attacker's own instance) delivers each of the `N` requests to the configured EVM destination and, for each, submits `claim_outbound_request_delivery_reward` with the destination state proof and signer attribution [7](#0-6) .
4. Each successful claim transfers `REWARD` from the `hb/trsry` treasury account to the payee, with no cap on `N` or on repeated invocation of step 2 — the attacker/relayer pair can repeat this indefinitely, draining the treasury at `N × REWARD` per batch for the cost of gas plus destination-delivery cost, which is far below the reward amount (10 tBRIDGE per request in the observed registration) [11](#0-10) .

### Citations

**File:** docs/outbound-request-incentivization.md (L9-11)
```markdown
A regular cross-chain message that flows *through* hyperbridge has a fee attached at origin (the source chain transfers `fee.payer → RELAYER_FEE_ACCOUNT` and records `RequestPayments[commitment]` in pallet-hyperbridge's child trie). When a relayer delivers and the destination receipt lands back on hyperbridge, the existing `accumulate_fees` flow credits that fee to the relayer. That whole pipeline assumes a *user* paid at origin.

But hyperbridge itself originates requests too: host parameter propagation, host-executive updates, intents-coprocessor responses, token-governor messages, the relayer pallet's withdrawal request. Today these all dispatch with `FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() }` (see `modules/pallets/host-executive/src/lib.rs:228`, `modules/pallets/intents-coprocessor/src/lib.rs:486`, `modules/pallets/relayer/src/lib.rs:638`, and `modules/pallets/token-governor/src/impls.rs`). Zero fee, zero payer. So relayers have no economic reason to pick them up, and the only thing that keeps them flowing today is altruism.
```

**File:** docs/outbound-request-incentivization.md (L112-142)
```markdown
### Verification pipeline

`process_outbound_request_delivery_claim` runs these checks in order. Ordering is deliberate: every cheap rejection happens before the state-proof verification, so non-allowlisted claims and replays are dropped without ever touching the trie verifier.

1. **Hash the request.** `commitment = hash_request::<IsmpHost>(&Request::Post(request))`. The relayer never gets to pick the commitment.

2. **Source check.** `request.source` must equal `IsmpHost::host_state_machine()`. Rejects forged claims for requests that didn't originate on this hyperbridge instance.

3. **Local presence check.** `pallet_ismp::child_trie::RequestCommitments::get(commitment).is_some()`. Defence in depth on top of the source check: the dispatcher already enforces source on insert, so anything missing from the trie was never dispatched here.

4. **Idempotency.** Reject if `OutboundRequestsClaimed[commitment]` is set.

5. **Module-id bound.** `BoundedVec::<u8, ModuleIdBound>::try_from(request.from.clone())`. Anything longer than 64 bytes is treated as not on the allowlist.

6. **Allowlist lookup.** `reward = OutboundRequestDeliveryReward::<T>::get(module_id)`. If zero, reject. This is the only place the allowlist is enforced; governance enables a module by setting a non-zero reward.

7. **State-machine match.** `state_proof.height.id.state_id == request.dest`. Defends against a relayer building a proof against a different chain than the request was sent to.

8. **Destination type and receipt key.** Use the `Pallet::request_receipt_key` helper (defined alongside the claim in `outbound_request.rs`):
   - EVM destinations: 32-byte slot hash `derive_unhashed_map_key(commitment, REQUEST_RECEIPTS_SLOT)`, the same key the EVM state machine's `receipts_state_trie_key` produces.
   - Substrate destinations: `pallet_ismp::child_trie::RequestReceipts::<T>::storage_key(commitment)`, identical to the substrate state machine's receipt key.

   A destination that is neither EVM nor substrate is rejected with `OutboundRequestUnsupportedDestination`.

9. **State proof verification.** Resolve the destination client with `ismp::handlers::validate_state_machine(&host, height)`, then `verify_withdrawal_proof(state_machine, &state_proof, vec![key])` against hyperbridge's stored state commitment for the destination. A verification failure maps to `OutboundDestinationStateNotKnown` (no commitment at that height), and a missing or null slot value maps to `OutboundDeliveryNotProven`.

10. **Signature attribution.** Recover the signer from `signature.verify(&outbound_request_delivery_message(commitment, destination, payee), None)` and check it matches the address proven in the receipt slot. For EVM, both are 20-byte addresses; for substrate, the bytes from the receipt must equal `signature.signer()`. Mismatch → `OutboundRequestSignerMismatch`.

11. **Payout.** Transfer `reward` from the treasury PalletId account to `payee`.

12. **Persist and emit.** Insert `OutboundRequestsClaimed[commitment] = ()`. Deposit `OutboundRequestDeliveryRewarded { commitment, state_machine: destination, module_id, relayer: payee, amount: reward }`.
```

**File:** docs/outbound-request-incentivization.md (L317-346)
```markdown
### Layer 4: manual runbook on gargantua with the ping module

This is the end to end smoke test, and it is the chosen approach for manual verification: run it against the live gargantua testnet rather than a locally spawned stack. The team holds the gargantua testnet sudo key, so the one privileged step (registering the reward) is available, and this avoids standing up a relay chain, collator, and BEEFY prover. The fully local alternative is documented in the next section for the case where testnet sudo is not available or a fully uncontested run is wanted.

It uses `pallet-ismp-demo` (the "ping" pallet, `IsmpDemo` in the gargantua runtime) as the hyperbridge-originated request producer. Its `dispatch_to_evm` extrinsic dispatches a `PostRequest` with `from = PALLET_ID.to_bytes()`, and `PALLET_ID` is `ModuleId::Pallet(PalletId(*b"ismp-ast"))`, so the module id on the wire is the raw 8 bytes `b"ismp-ast"` (`0x69736d702d617374`). That is the value governance registers a reward for.

One caveat for the live testnet: delivery is a race. Other relayers on the testnet also see `b"ismp-ast"` in their allowlist snapshot once the reward is registered, so whichever relayer lands the destination delivery first wins the reward (see the race explanation in the next section). To reliably observe your own relayer claiming, point it at an EVM destination other testnet relayers are not actively serving. The feature still proves out either way, you just may not be the payee.

Runtime facts that shape the steps:

- `pallet_ismp_relayer::Config::RelayerOrigin` on gargantua is `EnsureRoot`, so `set_outbound_request_delivery_reward` has to go through `Sudo::sudo`.
- The reward is paid from `TreasuryPalletId = PalletId(*b"hb/trsry")`. That account must hold enough balance or the claim fails with `OutboundRequestRewardTransferFailed`.
- Gargantua's host state machine is `StateMachine::Kusama(<para_id>)` (para id `4009` on the current testnet).

**Prerequisites:**

- Access to the live gargantua testnet with the sudo key in hand (the team holds it).
- An EVM destination chain reachable by the relayer, with the ISMP host contract deployed. `dispatch_to_evm` targets `StateMachine::Evm(destination)`.
- The consolidated tesseract relayer built from this branch, configured with gargantua as the hyperbridge source and that EVM chain as an outbound destination with a signer. Fees enabled, so the DB-backed claim pipeline runs.

**Steps:**

1. **Fund the treasury.** Confirm the `hb/trsry` account holds at least the reward amount, or top it up via `Sudo::sudo(Balances::force_set_balance(treasury_account, amount))`. The account id is `PalletId(*b"hb/trsry")` run through `into_account_truncating()`, which is `0x6d6f646c68622f7472737279` padded with zeros to 32 bytes (`0x6d6f646c68622f74727372790000000000000000000000000000000000000000`). On the current testnet this account already holds ~9.99M tBRIDGE, so no top-up was needed.
2. **Register the reward.** `Sudo::sudo(Relayer::set_outbound_request_delivery_reward(module_id = 0x69736d702d617374, amount = REWARD))`. The deployed run used `REWARD = 10000000000000` (10 tBRIDGE, the token has 12 decimals). Confirm the `OutboundRequestDeliveryRewardUpdated` event fired and `Relayer::OutboundRequestDeliveryReward(0x69736d702d617374)` now reads that amount.
3. **Let the relayer pick up the allowlist.** The outbound task calls `incentivized_outbound_request_modules` once per BEEFY notification, so within one cycle its snapshot includes `b"ismp-ast"`. Nothing to do here except wait one cycle before step 4.
4. **Dispatch from the ping module.** Call `IsmpDemo::dispatch_to_evm(EvmParams { module, destination, timeout, count: 1 })` from any signed account. `module` is the destination EVM module address, `destination` is the EVM chain id. This dispatches a `PostRequest` with `source = Kusama(4009)`, `from = b"ismp-ast"`, `dest = Evm(destination)`, and writes it into `RequestCommitments`.
5. **Watch delivery.** The outbound task picks up the BEEFY proof, keeps the request through `filter_events` (no `module_filter` configured, so `is_allowed_module` permits it) and `retain_incentivized_requests` (`b"ismp-ast"` is in the snapshot), delivers the batch to the EVM chain, then persists a claim row and pushes a `PendingRequestDeliveryClaim`.
6. **Watch the claim.** The outbound-request-claim task waits for gargantua's consensus client for the EVM chain to verify the delivery height, builds the `RequestReceipts[commitment]` state proof, signs, and submits `claim_outbound_request_delivery_reward`. Look for the log line `submitting outbound request delivery claim to hyperbridge`.
7. **Verify on gargantua.**
   - `OutboundRequestDeliveryRewarded { commitment, module_id, relayer, amount }` fired with `module_id = 0x69736d702d617374`.
```

**File:** docs/outbound-request-incentivization.md (L424-434)
```markdown
### Reward registration call

The hex-encoded sudo call that registered the ping-module reward (`Sudo` pallet index `25`, `Relayer` pallet index `53`, `set_outbound_request_delivery_reward` call index `6`):

```
0x1900350620 69736d702d617374 070010a5d4e80000000000000000000000
```

That set `OutboundRequestDeliveryReward[b"ismp-ast"] = 10_000_000_000_000` (10 tBRIDGE, 12 decimals). `OutboundRequestDeliveryRewardUpdated` fired with `newReward: 10,000,000,000,000`.

Note on shape: the reward is keyed by `module_id` alone (`StorageMap`), matching the design section above. A module's reward applies to its requests to any destination. If per-destination granularity is ever needed, widening to a `(destination, module_id)` double map is a follow-up.
```

**File:** docs/outbound-request-incentivization.md (L497-499)
```markdown
## Open questions

1. **One commitment, one reward, regardless of message size.** The consensus claim is a flat per-destination value. Do we want the request claim flat too, or scaled by request body size? Flat is simpler and matches the creator's wording ("award BRIDGE tokens"). Recommend flat for v1 and revisit if operators report it under or overpaying.
```

**File:** modules/pallets/relayer/src/lib.rs (L174-177)
```rust
	#[pallet::storage]
	#[pallet::getter(fn outbound_request_delivery_reward)]
	pub type OutboundRequestDeliveryReward<T: Config> =
		StorageMap<_, Blake2_128Concat, BoundedVec<u8, ModuleIdBound>, BalanceOf<T>, ValueQuery>;
```

**File:** modules/pallets/relayer/src/lib.rs (L433-450)
```rust
		/// Governance-set per-`module_id` reward for delivering a
		/// hyperbridge-originated request from that module. Setting
		/// `amount = 0` removes the module from the allowlist.
		#[pallet::call_index(6)]
		#[pallet::weight(<T as frame_system::Config>::DbWeight::get().reads_writes(0, 1))]
		pub fn set_outbound_request_delivery_reward(
			origin: OriginFor<T>,
			module_id: BoundedVec<u8, ModuleIdBound>,
			amount: BalanceOf<T>,
		) -> DispatchResult {
			T::RelayerOrigin::ensure_origin(origin)?;
			OutboundRequestDeliveryReward::<T>::insert(&module_id, amount);
			Self::deposit_event(Event::OutboundRequestDeliveryRewardUpdated {
				module_id,
				new_reward: amount,
			});
			Ok(())
		}
```

**File:** modules/pallets/demo/src/lib.rs (L216-239)
```rust
		/// Dispatch request to a connected EVM chain.
		#[pallet::weight(Weight::from_parts(1_000_000, 0))]
		#[pallet::call_index(2)]
		pub fn dispatch_to_evm(origin: OriginFor<T>, params: EvmParams) -> DispatchResult {
			let origin = ensure_signed(origin)?;
			let post = DispatchPost {
				dest: StateMachine::Evm(params.destination),
				from: PALLET_ID.to_bytes(),
				to: params.module.0.to_vec(),
				timeout: params.timeout,
				body: b"Hello from polkadot".to_vec(),
			};
			let dispatcher = T::IsmpHost::default();
			for _ in 0..params.count {
				// dispatch the request
				dispatcher
					.dispatch_request(
						DispatchRequest::Post(post.clone()),
						FeeMetadata { payer: origin.clone(), fee: Default::default() },
					)
					.map_err(|_| Error::<T>::TransferFailed)?;
			}
			Ok(())
		}
```

**File:** modules/pallets/ismp/src/dispatcher.rs (L92-146)
```rust
	fn dispatch_request(
		&self,
		request: DispatchRequest,
		fee: FeeMetadata<T>,
	) -> Result<H256, anyhow::Error> {
		// collect payment for the request
		if fee.fee != Zero::zero() {
			T::Currency::transfer(
				&fee.payer,
				&RELAYER_FEE_ACCOUNT.into_account_truncating(),
				fee.fee,
				Preservation::Expendable,
			)
			.map_err(|err| IsmpError::Custom(format!("Error withdrawing request fees: {err:?}")))?;
		}

		let request = match request {
			DispatchRequest::Get(dispatch_get) => {
				let get = GetRequest {
					source: self.host_state_machine(),
					dest: dispatch_get.dest,
					nonce: self.next_nonce(),
					from: dispatch_get.from,
					keys: dispatch_get.keys,
					height: dispatch_get.height,
					context: dispatch_get.context,
					timeout_timestamp: if dispatch_get.timeout == 0 {
						0
					} else {
						<T::TimestampProvider as UnixTime>::now()
							.as_secs()
							.saturating_add(dispatch_get.timeout)
					},
				};
				Request::Get(get)
			},
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
