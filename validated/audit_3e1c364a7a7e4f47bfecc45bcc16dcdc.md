## Title
Batch message handlers revert atomically on a single stale/duplicate entry, delaying delivery of all other valid requests/timeouts in the batch - (File: evm/src/core/HandlerV2.sol)

## Summary
The Notional report describes a class of bug where a batched operation processes multiple independent items but has a single `require`/revert condition tied to one item's live state; if that item's state changes between transaction construction and execution, the *entire* transaction reverts, delaying the (still valid) remaining items indefinitely. Hyperbridge's `HandlerV2` batch handlers (`handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, `handleGetRequestTimeouts`) exhibit the identical pattern: each function iterates over an array of independent requests/timeouts and reverts the *whole call* if any single entry fails a per-item liveness check (`DuplicateMessage`, `UnknownMessage`, `MessageNotTimedOut`).

## Finding Description
`handlePostRequests` first builds Merkle leaves for every request in the batch and verifies the aggregate multiproof, then in a second loop dispatches each request individually, reverting the whole call if any single request was already delivered: [1](#0-0) 

Because delivery is permissionless and multiple relayers legitimately race to deliver the same POST request (self-relaying users, competing relayer operators, or Hyperbridge's own relayer network retrying), it is common for two batches assembled around the same time to overlap on one request. If relayer A's individual/batch transaction for that one request lands first, relayer B's larger batch — which may bundle many *other*, still-undelivered requests — will revert entirely at `revert DuplicateMessage()`, even though only one of the N requests in the batch is stale.

The same pattern exists in the timeout handlers, which loop over an array of timeouts/`meta.sender == address(0)` checks and abort the full batch on the first already-processed entry: [2](#0-1) [3](#0-2) 

and in `handleGetResponses`, which reverts on `UnknownMessage()` for a single stale response inside a batch of many: [4](#0-3) 

This atomicity is a deliberate design choice documented for `batchCall` itself ("If any call fails, the entire batch reverts"), and the relayer implementation explicitly groups many independent ISMP messages into one `batchCall`/one handler invocation to save gas: [5](#0-4) [6](#0-5) 

None of the four batch handlers perform a per-item "skip and continue" or use `try/catch` around the stale check — the up-front verification loop and the dispatch loop are both unconditional `revert`s, so a single stale/duplicate/already-timed-out element poisons the entire array, exactly mirroring `TreasuryAction._rebalanceCurrency`'s `require(hasCooldownPassed || isExternalLendingUnhealthy)` poisoning an entire multi-currency rebalance.

## Impact Explanation
When a batch reverts because of one stale entry:
- Legitimate POST requests, GET responses, and (most importantly) **timeout refunds** for the other N-1 items in the batch are delayed. Timeout processing directly gates the relayer-fee refund to the `payer` (`onPostRequestTimeout` must succeed before refund), so repeated collisions can materially delay fund refunds/state resolution across many pending messages, not just one.
- Under active/volatile relaying conditions (many relayers/self-relayers competing, as Hyperbridge explicitly supports self-relay alongside its relayer network), this can recur repeatedly, compounding delivery/timeout-refund delays similar to how the Notional rebalance was "delayed... resulting in excess liquidity being lent out," here resulting in outstanding cross-chain state (pending requests/timeouts) remaining unresolved for longer than intended, and relayers wasting gas repeatedly resubmitting shrinking batches.

This is a data-availability/finality-delay impact on the bridge's core message pipeline rather than a fund-theft primitive, but it degrades the liveness guarantee the timeout/refund design is built to provide.

## Likelihood Explanation
High under normal, non-malicious operating conditions: the same POST request commitment can legitimately be picked up by more than one relayer (self-relay + network relayer, or two competing relayer operators), and batches are typically constructed from a snapshot of pending messages that can go stale by the time the transaction is mined (network latency, gas competition, mempool reordering). No malicious actor, prover, or admin is required — this is a race between two honest, permissionless callers of an intentionally-permissionless function.

## Recommendation
In `handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, and `handleGetRequestTimeouts`, change the per-item duplicate/unknown/not-timed-out checks from `revert` to `continue` (skip that single entry) so the rest of the batch is still processed and delivered/timed-out, mirroring the Notional recommendation of skipping the now-invalid item instead of aborting the whole operation. Alternatively, keep `batchCall`'s all-or-nothing semantics for consensus/state-proof verification (which must be atomic) but make the innermost per-leaf dispatch loops best-effort/non-reverting, since the Merkle multiproof already guarantees the leaves' authenticity independent of their current on-chain receipt status.

## Proof of Concept
1. Relayer A submits `handlePostRequests` (or `handlePostRequestTimeouts`) for request `R1` alone; it lands in block `N`.
2. Relayer B, having snapshotted pending messages slightly earlier, submits a batch `handlePostRequests([R1, R2, ..., R10])` (or the equivalent `batchCall`) that also includes `R1`, in block `N+1`.
3. At execution time, the loop at `evm/src/core/HandlerV2.sol:204-209` reaches `R1`, finds `host.requestReceipts(R1.hash()) != address(0)` (already set by relayer A), and reverts `DuplicateMessage()` for the entire transaction — `R2..R10`, which were still valid and undelivered, are not dispatched and must wait for a resubmission.
4. Repeat with `handlePostRequestTimeouts`/`handleGetRequestTimeouts`: if any one timeout in `message.timeouts` was already resolved (`meta.sender == address(0)`, line 275/310), the whole timeout batch reverts at `UnknownMessage()`, delaying refunds for all other timed-out requests bundled in that call.

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

**File:** evm/src/core/HandlerV2.sol (L241-246)
```text
        for (uint256 i = 0; i < responsesLength; ++i) {
            GetResponseLeaf memory leaf = message.responses[i];
            // duplicate response?
            if (host.responseReceipts(leaf.response.request.hash()).relayer != address(0)) revert DuplicateMessage();
            host.dispatchIncoming(leaf.response, _msgSender());
        }
```

**File:** evm/src/core/HandlerV2.sol (L267-285)
```text
        for (uint256 i = 0; i < timeoutsLength; ++i) {
            PostRequest memory request = message.timeouts[i];
            // timed-out?
            if (request.timeout() > state.timestamp) revert MessageNotTimedOut();

            // known request? also serves as source check
            bytes32 requestCommitment = request.hash();
            FeeMetadata memory meta = host.requestCommitments(requestCommitment);
            if (meta.sender == address(0)) revert UnknownMessage();

            bytes[] memory keys = new bytes[](1);
            keys[0] = bytes.concat(REQUEST_RECEIPTS_STORAGE_PREFIX, requestCommitment);

            // verify state trie non-membership proofs
            PolkadotTrie.StorageValue memory entry = PolkadotTrie.VerifyProof(state.stateRoot, message.proof, keys)[0];
            if (entry.value.length != 0) revert InvalidProof();

            host.dispatchTimeOut(PostRequestTimeout(request, _msgSender()), meta, requestCommitment);
        }
```

**File:** evm/src/core/HandlerV2.sol (L303-320)
```text
        for (uint256 i = 0; i < timeoutsLength; ++i) {
            GetRequest memory request = message.timeouts[i];
            // timed-out?
            if (request.timeout() > state.timestamp) revert MessageNotTimedOut();

            bytes32 commitment = request.hash();
            FeeMetadata memory meta = host.requestCommitments(commitment);
            if (meta.sender == address(0)) revert UnknownMessage();

            bytes[] memory keys = new bytes[](1);
            keys[0] = bytes.concat(RESPONSE_RECEIPTS_STORAGE_PREFIX, commitment);

            // verify state trie non-membership proofs
            PolkadotTrie.StorageValue memory entry = PolkadotTrie.VerifyProof(state.stateRoot, message.proof, keys)[0];
            if (entry.value.length != 0) revert InvalidProof();

            host.dispatchTimeOut(GetRequestTimeout(request, _msgSender()), meta, commitment);
        }
```

**File:** tesseract/messaging/evm/src/tx.rs (L340-389)
```rust
async fn build_batch_inner_calls(
	client: &EvmClient,
	messages: &[Message],
) -> anyhow::Result<Vec<Bytes>> {
	let handler_addr = Address::from_slice(&client.handler().await?.0);
	let contract = HandlerV2Instance::new(handler_addr, client.signer.clone());
	let ismp_host = Address::from_slice(&client.ismp_host.0);

	let mut inner = Vec::with_capacity(messages.len());
	for msg in messages {
		let calldata = match msg {
			Message::Consensus(msg) => contract
				.handleConsensus(ismp_host, Bytes::from(msg.consensus_proof.clone()))
				.calldata()
				.clone(),

			Message::Request(msg) => {
				let (mmr_proof, leaf_indices) = decode_mmr_proof(&msg.proof.proof)?;
				let mut leaves: Vec<PostRequestLeaf> = msg
					.requests
					.iter()
					.zip(&leaf_indices)
					.map(|(post, &leaf_index)| PostRequestLeaf {
						request: post.clone().into(),
						index: AlloyU256::from(leaf_index),
					})
					.collect();
				leaves.sort_by_key(|l| l.index);
				let proof = build_solidity_proof(&mmr_proof, &msg.proof.height)?;
				contract
					.handlePostRequests(ismp_host, PostRequestMessage { proof, requests: leaves })
					.calldata()
					.clone()
			},

			Message::Response(msg) => {
				let message = build_get_response_message(msg)?;
				contract.handleGetResponses(ismp_host, message).calldata().clone()
			},

			Message::Timeout(_) =>
				return Err(anyhow!("Timeout messages are not supported by batchCall")),

			Message::FraudProof(_) =>
				return Err(anyhow!("Unexpected fraud proof message in batchCall")),
		};
		inner.push(calldata);
	}
	Ok(inner)
}
```

**File:** tesseract/messaging/evm/src/tx.rs (L441-446)
```rust
/// Submit a full batch of ISMP messages as a single `IHandlerV2.batchCall` transaction.
///
/// One tx replaces what would otherwise be N separate txs (one per message),
/// cutting gas overhead and nonce management complexity. Atomic: if any
/// inner call reverts, the whole transaction reverts.
pub async fn submit_batch_messages(
```
