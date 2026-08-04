### Title
Commitment mismatch between on-chain `Message.hash` and off-chain SDK/indexer commitment computation via lossy `string` round-trip of `source`/`dest` — ([File: sdk/packages/core/contracts/libraries/Message.sol])

### Summary
`EvmHost.dispatch(DispatchPost)` computes and stores the canonical request commitment using the raw `bytes` values of `source`/`dest`, but re-emits those same fields as ABI `string` in `PostRequestEvent`. Off-chain consumers (the indexer's `computeRequestCommitment` and the SDK's `postRequestCommitment`) reconstruct the commitment by re-encoding the *decoded* JS string back to UTF‑8 bytes. If `dest` (fully attacker-controlled via `DispatchPost.dest`) contains a byte sequence that is not valid UTF‑8, the string decode/re-encode round trip is lossy, causing the off-chain-computed commitment to differ from the true on-chain commitment.

### Finding Description
`EvmHost.dispatch` builds the `PostRequest` struct straight from caller-supplied fields and hashes it with `Message.hash`, which is simply `keccak256(abi.encode(req))` over the raw `bytes` fields: [1](#0-0) [2](#0-1) 

The same dispatch call then emits `PostRequestEvent`, but with `source`/`dest` typed as Solidity `string` rather than `bytes`: [3](#0-2) [4](#0-3) 

The `string(request.dest)` cast performs no UTF‑8 validation — it is a bare reinterpretation of the same bytes — so the raw bytes on the wire are unchanged from what was hashed. The divergence appears off-chain: when clients decode this ABI `string` field, they materialize a JS string via UTF‑8 text decoding. If the underlying bytes are not valid UTF‑8, this decode is lossy (invalid byte sequences are replaced, per standard UTF‑8 decoder behavior). Both the indexer and the SDK then reconstruct the commitment from this already-decoded string by converting it back to bytes: [5](#0-4) [6](#0-5) [7](#0-6) 

Since `toUtf8Bytes`/`toHex` re-encode the *lossy* string, not the original raw bytes, `abi.encode` over the reconstructed tuple no longer matches `abi.encode(request)` computed on-chain in `Message.hash`. The resulting `keccak256` therefore differs from the actual key under which `EvmHost._requestCommitments[commitment]` (the `FeeMetadata{sender, fee}`) was stored: [8](#0-7) 

Nothing in the dispatch path validates that `post.dest` (or `from`/`to`/`body`) is UTF‑8-safe; an unprivileged caller fully controls `DispatchPost.dest` when invoking `dispatch`: [9](#0-8) 

### Impact Explanation
Because the SDK's `postRequestCommitment`/indexer's `computeRequestCommitment` are the mechanisms relayers, indexers, and application clients use both to (a) track/locate a request's fee-funded commitment and (b) reconstruct the exact `PostRequest` struct needed to build delivery calldata for `HandlerV2`/timeout proofs, a mismatch here means:
- Relayers/indexers cannot correlate the emitted event with the true on-chain `_requestCommitments` entry, so the request is effectively invisible to tooling built on this commitment.
- Any delivery/timeout submission built from the (corrupted) reconstructed `PostRequest.dest` will hash to a value that does not match the real commitment proven via consensus/state proofs, causing verification to fail on the destination or during timeout.
- The escrowed relayer fee stored against the real commitment (`FeeMetadata.sender/fee`) becomes permanently unclaimable — it can neither be delivered (wrong reconstructed request never matches the true commitment) nor refunded via timeout (the timeout path also needs to reproduce the exact original hash to match `_requestCommitments`).

This matches the bounty's "wrong commitment / broken timeout / refund accounting" impact class and can permanently strand payer-escrowed fees.

### Likelihood Explanation
Exploitation requires only an unprivileged call to `dispatch(DispatchPost)` with a `dest` (or, in principle, `from`/`to`) value that is not valid UTF‑8 — no privileged access, no relayer/prover collusion, and no reliance on malicious infrastructure. Any application built on Hyperbridge that lets end users influence the destination-module encoding, or that a malicious integrator deliberately crafts, can trigger this. Note: conventional `dest` values produced via `StateMachine.evm(...)`/`StateMachine.polkadot(...)` helper libraries are always ASCII, so under normal, honest usage this never triggers — the risk materializes specifically when an unprivileged caller deliberately supplies malformed/non-UTF‑8 bytes as `dest`.

### Recommendation
- Do not round-trip `source`/`dest` through JS `string` decoding for commitment purposes. Emit and consume these fields consistently as raw `bytes` throughout the indexer/SDK pipeline (matching the `bytes` type used in `Message.sol`/`PostRequest`), or have the SDK/indexer commitment functions accept and use the raw hex bytes captured from the log topic/data rather than the UTF‑8-decoded string.
- Alternatively/additionally, enforce ASCII/UTF‑8-safety of `source`/`dest` on-chain in `dispatch` (revert on invalid encoding) so the `string(...)` cast in the event can never be lossy.
- Add a cross-language differential test (as the question suggests) dispatching non-UTF‑8 `dest`/`from`/`to` and empty `body`, asserting `Message.hash` (Solidity) == `postRequestCommitment` (SDK) == `computeRequestCommitment` (indexer).

### Proof of Concept
1. Deploy `EvmHost` and call `dispatch(DispatchPost{ dest: hex"ff...", to: ..., body: "", fee: X, payer: user, timeout: T })` where `dest` bytes are not valid UTF‑8 (e.g., contains a lone continuation byte `0xC0`).
2. On-chain: `commitment = Message.hash(request)` computed from the raw bytes is stored in `_requestCommitments[commitment]`.
3. Off-chain: fetch the emitted `PostRequestEvent`, decode `dest` as an ABI `string` (lossy UTF‑8 decode → contains U+FFFD replacement characters), then call `computeRequestCommitment`/`postRequestCommitment` with this decoded string.
4. Compare: `commitment != computeRequestCommitment(...)` — assert inequality to demonstrate the mismatch (differential test as proposed in the question).

### Citations

**File:** evm/src/core/EvmHost.sol (L224-242)
```text
    // Emitted when a new POST request is dispatched
    event PostRequestEvent(
        // Source of this request, included for convenience sake
        string source,
        // The destination chain for this request
        string dest,
        // The contract that initiated this request
        address indexed from,
        // The intended recipient module of this request
        bytes to,
        // Monotonically increasing nonce
        uint256 nonce,
        // The timestamp at which this request will be considered as timed out
        uint256 timeoutTimestamp,
        // The serialized request body
        bytes body,
        // The associated relayer fee
        uint256 fee
    );
```

**File:** evm/src/core/EvmHost.sol (L921-933)
```text
    function dispatch(DispatchPost memory post) external payable notFrozen returns (bytes32 commitment) {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                post.fee, path, address(this), block.timestamp
            );
        } else if (post.fee > 0) {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), post.fee);
        }

```

**File:** evm/src/core/EvmHost.sol (L934-948)
```text
        // adjust the timeout
        uint64 timeoutTimestamp = post.timeout == 0 ? 0 : uint64(block.timestamp) + uint64(post.timeout);
        PostRequest memory request = PostRequest({
            source: host(),
            dest: post.dest,
            nonce: uint64(_nextNonce()),
            from: abi.encodePacked(_msgSender()),
            to: post.to,
            timeoutTimestamp: timeoutTimestamp,
            body: post.body
        });

        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: post.payer, fee: post.fee});
```

**File:** evm/src/core/EvmHost.sol (L949-958)
```text
        emit PostRequestEvent({
            source: string(request.source),
            dest: string(request.dest),
            from: _msgSender(),
            to: abi.encodePacked(request.to),
            nonce: request.nonce,
            timeoutTimestamp: request.timeoutTimestamp,
            body: request.body,
            fee: post.fee
        });
```

**File:** sdk/packages/core/contracts/libraries/Message.sol (L207-230)
```text
    function encode(PostRequest memory req) internal pure returns (bytes memory) {
        return abi.encode(req);
    }

    /**
     * @dev Encode the given get request for commitment
     */
    function encode(GetRequest memory req) internal pure returns (bytes memory) {
        return abi.encode(req);
    }

    /**
     * @dev Encode the given get response for commitment
     */
    function encode(GetResponse memory res) internal pure returns (bytes memory) {
        return abi.encode(res);
    }

    /**
     * @dev Returns the commitment for the given post request
     */
    function hash(PostRequest memory req) internal pure returns (bytes32) {
        return keccak256(encode(req));
    }
```

**File:** sdk/packages/indexer/src/services/request.service.ts (L238-251)
```typescript

		// Convert source/dest from state-machine strings ("EVM-97" etc.) to bytes.
		const sourceByte = ethers.utils.toUtf8Bytes(source)
		const destByte = ethers.utils.toUtf8Bytes(dest)

		// Mirror the EVM host's commitment: keccak256(abi.encode(PostRequest)),
		// with the outer tuple wrapper. Field order matches the PostRequest struct
		// in core/libraries/Message.sol: source, dest, nonce, from, to, timeoutTimestamp, body.
		const encoded = ethers.utils.defaultAbiCoder.encode(
			["tuple(bytes,bytes,uint64,bytes,bytes,uint64,bytes)"],
			[[sourceByte, destByte, nonce, from, to, timeoutTimestamp, body]],
		)
		return ethers.utils.keccak256(encoded)
	}
```

**File:** sdk/packages/indexer/src/handlers/events/evmHost/postRequest.event.handler.ts (L26-54)
```typescript
	const { transaction, blockNumber, transactionHash, args, block } = event
	let { dest, fee, from, nonce, source, timeoutTimestamp, to, body } = args

	const chain: string = getHostStateMachine(chainId)
	const timestamp = await getBlockTimestamp(block.hash, chain)

	logger.info(
		`Computing RequestV2 Commitment Event: ${stringify({
			dest,
			fee,
			from,
			nonce,
			source,
			timeoutTimestamp,
			to,
			body,
		})}`,
	)

	// Compute the request commitment
	let request_commitment = RequestService.computeRequestCommitment(
		source,
		dest,
		BigInt(nonce.toString()),
		BigInt(timeoutTimestamp.toString()),
		from,
		to,
		body,
	)
```

**File:** sdk/packages/sdk/src/utils.ts (L202-238)
```typescript
export function postRequestCommitment(post: IPostRequest): { commitment: HexString; encodePacked: HexString } {
	// Mirror the EVM host: keccak256(abi.encode(PostRequest)) with the outer tuple
	// wrapper. Field order matches core/libraries/Message.sol#PostRequest:
	// source, dest, nonce, from, to, timeoutTimestamp, body.
	const data = encodeAbiParameters(
		[
			{
				type: "tuple",
				components: [
					{ name: "source", type: "bytes" },
					{ name: "dest", type: "bytes" },
					{ name: "nonce", type: "uint64" },
					{ name: "from", type: "bytes" },
					{ name: "to", type: "bytes" },
					{ name: "timeoutTimestamp", type: "uint64" },
					{ name: "body", type: "bytes" },
				],
			},
		],
		[
			{
				source: toHex(post.source),
				dest: toHex(post.dest),
				nonce: post.nonce,
				from: post.from,
				to: post.to,
				timeoutTimestamp: post.timeoutTimestamp,
				body: post.body,
			},
		],
	)

	return {
		commitment: keccak256(data),
		encodePacked: data,
	}
}
```
