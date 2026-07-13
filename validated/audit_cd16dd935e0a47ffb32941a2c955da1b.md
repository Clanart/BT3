The bug is confirmed. The root cause is in `rpc/stream/rpc.go` line 199, not in `websockets.go` itself.

---

### Title
`newHeads` WebSocket Subscription Delivers CometBFT Block Hash Instead of Ethereum keccak256 Header Hash - (`rpc/stream/rpc.go`)

### Summary
The `RPCStream.start` goroutine populates `RPCHeader.Hash` with the CometBFT SHA256-based block hash. `subscribeNewHeads` then emits that value verbatim as the Ethereum `hash` field in every `newHeads` notification. The two hash schemes are cryptographically incompatible, so every hash delivered over this public WebSocket path is wrong by construction.

### Finding Description
In `RPCStream.start`, when a `tmtypes.EventDataNewBlock` arrives, the header is published as:

```go
s.headerStream.Add(RPCHeader{EthHeader: header, Hash: common.BytesToHash(data.Block.Header.Hash())})
``` [1](#0-0) 

`data.Block.Header.Hash()` is the CometBFT Merkle/SHA256 hash of the Tendermint header protobuf — a completely different structure and hash function from `keccak256(rlp(ethHeader))` required by the Ethereum spec.

`subscribeNewHeads` then copies this value directly into the JSON response:

```go
enc.Hash = header.Hash
``` [2](#0-1) 

The `Header` struct's `Hash` field is the one serialised to JSON as `"hash"` in the `newHeads` notification: [3](#0-2) 

### Impact Explanation
Any unprivileged client subscribing to `eth_subscribe newHeads` over the public WebSocket endpoint receives a block hash that will never match the result of `eth_getBlockByHash`, `eth_getBlockByNumber`, or any receipt/log lookup keyed by block hash. Downstream effects include:

- Bridges and relayers that verify finality by block hash will silently operate on wrong hashes, potentially accepting or rejecting blocks incorrectly.
- Clients performing chain-tip tracking or fork detection using the announced hash will diverge from the actual chain state.
- Any tool that cross-checks `newHeads.hash` against `eth_getBlockByNumber` will detect a permanent mismatch, breaking standard Ethereum tooling assumptions.

This is a public JSON-RPC path delivering incorrect consensus-critical data, matching the High impact category.

### Likelihood Explanation
The path is unconditionally reachable by any unauthenticated WebSocket client. No special conditions are required — every `newHeads` event is affected on every block.

### Recommendation
Replace the CometBFT hash with the Ethereum keccak256 header hash. After constructing the `ethtypes.Header` via `types.EthHeaderFromTendermint`, compute:

```go
ethHash := header.Hash() // ethtypes.Header.Hash() = keccak256(rlp(header))
s.headerStream.Add(RPCHeader{EthHeader: header, Hash: ethHash})
``` [4](#0-3) 

This ensures the hash published over `newHeads` is identical to the hash returned by `eth_getBlockByNumber`/`eth_getBlockByHash`.

### Proof of Concept
1. Connect to the WebSocket endpoint and send:
   ```json
   {"jsonrpc":"2.0","id":1,"method":"eth_subscribe","params":["newHeads"]}
   ```
2. Wait for a `newHeads` notification; record `result.hash` and `result.number`.
3. Call `eth_getBlockByNumber` with the same block number; record its `hash`.
4. Assert equality — the two hashes will differ on every block, confirming the CometBFT hash is being delivered instead of the Ethereum hash. [1](#0-0) [5](#0-4)

### Citations

**File:** rpc/stream/rpc.go (L189-199)
```go
			header := types.EthHeaderFromTendermint(data.Block.Header, ethtypes.Bloom{}, baseFee, validator)
			txHash, err := evmTxHashFromEventData(data, s.txDecoder)
			if err != nil {
				// Drop rather than publish a wrong transactionsRoot; a gap in
				// the stream is better than incorrect data cached by clients.
				s.logger.Error("failed to compute transactionsRoot for newHeads, dropping header",
					"height", data.Block.Height, "err", err)
				continue
			}
			header.TxHash = txHash
			s.headerStream.Add(RPCHeader{EthHeader: header, Hash: common.BytesToHash(data.Block.Header.Hash())})
```

**File:** rpc/websockets.go (L583-585)
```go
	// overwrite rlpHash
	Hash common.Hash `json:"hash"`
}
```

**File:** rpc/websockets.go (L591-622)
```go
	go api.events.HeaderStream().Subscribe(ctx, func(headers []stream.RPCHeader, _ int) error {
		for _, header := range headers {
			h := header.EthHeader
			var enc Header
			enc.ParentHash = h.ParentHash
			enc.UncleHash = h.UncleHash
			enc.Coinbase = h.Coinbase.Hex()
			enc.Root = h.Root
			enc.TxHash = h.TxHash
			enc.ReceiptHash = h.ReceiptHash
			enc.Bloom = h.Bloom
			enc.Difficulty = (*hexutil.Big)(h.Difficulty)
			enc.Number = (*hexutil.Big)(h.Number)
			enc.GasLimit = hexutil.Uint64(h.GasLimit)
			enc.GasUsed = hexutil.Uint64(h.GasUsed)
			enc.Time = hexutil.Uint64(h.Time)
			enc.Extra = h.Extra
			enc.MixDigest = h.MixDigest
			enc.Nonce = h.Nonce
			enc.BaseFee = (*hexutil.Big)(h.BaseFee)
			enc.WithdrawalsHash = h.WithdrawalsHash
			if h.BlobGasUsed != nil {
				bgu := hexutil.Uint64(*h.BlobGasUsed)
				enc.BlobGasUsed = &bgu
			}
			if h.ExcessBlobGas != nil {
				ebg := hexutil.Uint64(*h.ExcessBlobGas)
				enc.ExcessBlobGas = &ebg
			}
			enc.ParentBeaconRoot = h.ParentBeaconRoot
			enc.RequestsHash = h.RequestsHash
			enc.Hash = header.Hash
```
