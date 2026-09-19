### Title
`broadcast_tx_*` Tendermint RPC bypasses Autobahn EVM shard routing, letting a client submit an EVM tx directly to a non-owning validator's local mempool - (File: sei-tendermint/internal/rpc/core/mempool.go)

### Summary
Sei's Autobahn sharded-execution mode splits the EVM address space across validators, and `EvmProxy` is the mechanism that is supposed to make sure a transaction always reaches the validator that "owns" the sender's shard. The `eth_sendRawTransaction` JSON-RPC path implements this correctly, but the underlying Cosmos/Tendermint `broadcast_tx_sync` / `broadcast_tx_async` / `broadcast_tx_commit` RPC endpoints do not perform the same shard-ownership check — exactly the "one RPC surface forwards, the other silently doesn't" pattern described in the referenced LES report.

### Finding Description
`evmrpc/send.go`'s `SendAPI.SendRawTransaction` explicitly resolves the tx sender and checks `s.tmClient.EvmProxy(sender)`; if the local validator does not own that sender's shard, the request is forwarded via `eth_sendRawTransaction` to the shard owner instead of being processed locally: [1](#0-0) 

The equivalent EVM-only Autobahn RPC server (`giga/evmonly/rpc/server.go`) implements the same pattern — check `EvmProxy`, forward remotely if the sender belongs elsewhere, otherwise broadcast locally: [2](#0-1) 

However, the generic Tendermint RPC methods that every Cosmos client uses to submit a signed transaction (`broadcast_tx_async`, `broadcast_tx_sync`, `broadcast_tx_commit` — reachable via gRPC/REST `sei-cosmos/x/auth/tx/service.go`, `sei-cosmos/client/broadcast.go`, or any raw JSON-RPC call) go through `Environment.BroadcastTx`/`BroadcastTxAsync` in `sei-tendermint/internal/rpc/core/mempool.go`. When Autobahn is active and the local node is a validator (has a local `giga.Mempool()`), the code inserts the transaction directly into the **local** mempool via `v.InsertTx`/`v.TryInsertTx` — with **no call to `EvmProxy`, no shard check at all**: [3](#0-2) 

Compare this to `EvmProxy` itself, which is defined right above and is clearly meant to be the gatekeeping check before any EVM transaction is accepted locally: [4](#0-3) 

And the validator's `EvmProxy` implementation makes explicit that self-shard txs are handled "locally via mempool" while cross-shard sends must be proxied: [5](#0-4) 

The producer mempool (`sei-tendermint/internal/autobahn/producer/mempool.go`), which is what `InsertTx`/`TryInsertTx` ultimately call, performs nonce/gas/size checks via `CheckTxSafe` but contains no shard-ownership validation either — it will happily accept and sequence any well-formed EVM transaction regardless of whether the local validator actually owns that sender's address shard: [6](#0-5) 

So a transaction submitted through `eth_sendRawTransaction` is correctly routed to the shard owner, but the exact same transaction submitted through the standard Cosmos SDK tx-broadcast RPC (`broadcast_tx_sync`, used by `seid tx broadcast`, the gRPC `tx.Service/BroadcastTx`, REST `POST /cosmos/tx/v1beta1/txs`, or any Cosmos SDK client library) is inserted into whatever validator happens to receive the request, even if that validator does not own the sender's shard.

### Impact Explanation
This is a direct architectural analog of the LES bug: an alternate, "overlooked" transaction-submission RPC surface skips the mandatory forwarding/routing logic that the primary surface implements. Concretely:
- A transaction can be sequenced into a lane block by a validator that does not own the sender's shard. Given Autobahn's design (each validator owns a shard of address space and is presumably the sole party expected to execute/finalize state for that shard), accepting and sequencing a wrong-shard transaction can produce incorrect nonce bookkeeping (`s.app.EvmNonce(addr)` queried against a validator that never executes that address's state), duplicate/conflicting sequencing versus what the actual shard owner independently receives, and inconsistent global block construction across validators — undermining the safety guarantees the shard-routing design is meant to provide.
- From a UX standpoint it mirrors the original bug exactly: the client receives a successful broadcast result (a tx hash) via a legitimate, publicly documented RPC method, without any indication that shard-routing was bypassed, while the sequencing/execution semantics differ from what `eth_sendRawTransaction` (the intended entry point) guarantees.

### Likelihood Explanation
Reaching this path requires only a normal, unprivileged Cosmos-signed `MsgEVMTransaction` broadcast via the standard Cosmos tx-broadcast RPC/gRPC/REST endpoints instead of `eth_sendRawTransaction` — no special privileges, malicious peers, or governance actions are needed. Any wallet, SDK, or script that submits EVM transactions the "Cosmos way" (which is fully supported since `MsgEVMTransaction` is a normal Cosmos message) instead of via `eth_sendRawTransaction` will hit this path on an Autobahn validator.

### Recommendation
Route `Environment.BroadcastTxAsync`/`BroadcastTx`/`BroadcastTxCommit` in `sei-tendermint/internal/rpc/core/mempool.go` through the same `EvmProxy` shard-ownership check used by `evmrpc/send.go` and `giga/evmonly/rpc/server.go` before calling `giga.Mempool().InsertTx`/`TryInsertTx`: decode the tx, extract the EVM sender (when applicable), consult `env.EvmProxy(sender)`, and forward the raw bytes to the shard owner's RPC when the local validator is not the owner, exactly mirroring the pattern in `evmrpc/send.go` lines 98-111.

### Proof of Concept
1. Stand up an Autobahn cluster with `EnableEvmProxy` on (the default), so validators normally proxy cross-shard sends.
2. Sign a `MsgEVMTransaction` for an EVM address whose shard (per `Committee().EvmShard(sender)`) is *not* owned by the validator you will target.
3. Instead of calling `eth_sendRawTransaction` against that validator's EVM JSON-RPC (which would trigger `SendAPI.SendRawTransaction`'s `EvmProxy` check and forward the tx), submit the same signed tx bytes directly via the validator's Cosmos/Tendermint RPC endpoint using `broadcast_tx_sync` (or the gRPC `tx.Service/BroadcastTx`, or REST `/cosmos/tx/v1beta1/txs`).
4. Observe in `sei-tendermint/internal/rpc/core/mempool.go`'s `BroadcastTx` that the code takes the `giga.Mempool()` branch and calls `v.InsertTx(ctx, req.Tx)` directly — no `EvmProxy` lookup occurs, unlike the `evmrpc/send.go` code path.
5. The transaction gets sequenced by the "wrong" validator's local producer mempool (`sei-tendermint/internal/autobahn/producer/mempool.go`), whose `insertTx` performs only nonce/gas/size checks (no shard check), diverging from the intended shard-routing invariant that `eth_sendRawTransaction` enforces.

### Citations

**File:** evmrpc/send.go (L98-111)
```go
	// getSender fails for AccessListTx, in which case we are not able to proxy or simulate,
	// but we still need to handle it.
	sender, senderErr := getSender(tx, s.keeper.ChainID(s.ctxProvider(LatestCtxHeight)))
	if senderErr == nil {
		if client, ok := s.tmClient.EvmProxy(sender).Get(); ok {
			recordRedirectedRequest(ctx, "eth_sendRawTransaction", string(s.connectionType))

			if err := client.CallContext(ctx, &hash, "eth_sendRawTransaction", input); err != nil {
				// No error wrapping, because evm server is too dumb to handle wrapped error.
				return hash, err
			}
			return hash, nil
		}
	}
```

**File:** giga/evmonly/rpc/server.go (L46-66)
```go
// SendRawTransaction submits a signed raw Ethereum transaction to Autobahn and
// returns its Ethereum transaction hash.
func (api *sendAPI) SendRawTransaction(ctx context.Context, input hexutil.Bytes) (common.Hash, error) {
	tx := new(ethtypes.Transaction)
	if err := tx.UnmarshalBinary(input); err != nil {
		return common.Hash{}, err
	}
	hash := tx.Hash()

	if sender, err := ethtypes.Sender(ethtypes.LatestSignerForChainID(tx.ChainId()), tx); err == nil {
		if client, ok := api.backend.EvmProxy(sender).Get(); ok {
			if err := client.CallContext(ctx, &hash, "eth_sendRawTransaction", input); err != nil {
				return hash, err
			}
			return hash, nil
		}
	}

	result, err := api.backend.BroadcastTx(ctx, &coretypes.RequestBroadcastTx{
		Tx: append(tmtypes.Tx(nil), input...),
	})
```

**File:** sei-tendermint/internal/rpc/core/mempool.go (L20-28)
```go
// EvmProxy returns the EVM RPC client of the autobahn validator that owns the
// sender shard, or None if the sender maps to the local validator (handle
// locally) or autobahn isn't configured.
func (env *Environment) EvmProxy(sender common.Address) utils.Option[*ethrpc.Client] {
	if r, ok := env.gigaRouter().Get(); ok {
		return r.EvmProxy(sender)
	}
	return utils.None[*ethrpc.Client]()
}
```

**File:** sei-tendermint/internal/rpc/core/mempool.go (L58-107)
```go
func (env *Environment) BroadcastTxAsync(ctx context.Context, req *coretypes.RequestBroadcastTx) (*coretypes.ResultBroadcastTx, error) {
	if err := env.requireWritable(); err != nil {
		return nil, err
	}
	if giga, ok := env.gigaRouter().Get(); ok {
		v, ok := giga.Mempool().Get()
		if !ok {
			return nil, errors.New("autobahn fullnode has no local mempool; broadcast_tx_* must be sent to a validator")
		}
		go func() { _, _ = v.TryInsertTx(ctx, req.Tx) }()
		return &coretypes.ResultBroadcastTx{Hash: req.Tx.Hash().Bytes()}, nil
	}
	mp, err := env.requireMempool()
	if err != nil {
		return nil, err
	}
	go func() { _, _ = mp.CheckTx(ctx, req.Tx) }()

	return &coretypes.ResultBroadcastTx{Hash: req.Tx.Hash().Bytes()}, nil
}

// Deprecated and should be remove in 0.37
func (env *Environment) BroadcastTxSync(ctx context.Context, req *coretypes.RequestBroadcastTx) (*coretypes.ResultBroadcastTx, error) {
	return env.BroadcastTx(ctx, req)
}

// BroadcastTx returns with the response from CheckTx. Does not wait for
// DeliverTx result.
// More: https://docs.tendermint.com/master/rpc/#/Tx/broadcast_tx_sync
func (env *Environment) BroadcastTx(ctx context.Context, req *coretypes.RequestBroadcastTx) (*coretypes.ResultBroadcastTx, error) {
	if err := env.requireWritable(); err != nil {
		return nil, err
	}
	if giga, ok := env.gigaRouter().Get(); ok {
		v, ok := giga.Mempool().Get()
		if !ok {
			return nil, errors.New("autobahn fullnode has no local mempool; broadcast_tx_* must be sent to a validator")
		}
		r, err := v.InsertTx(ctx, req.Tx)
		if err != nil {
			return nil, err
		}
		return &coretypes.ResultBroadcastTx{
			Code:      r.Code,
			Data:      r.Data,
			Codespace: r.Codespace,
			Hash:      req.Tx.Hash().Bytes(),
			Log:       r.Log,
		}, nil
	}
```

**File:** sei-tendermint/internal/p2p/giga_router_validator.go (L128-144)
```go
// EvmProxy on the validator returns None when the sender's shard owner is
// us (handle locally via mempool). For remote
// shards, we proxy only while the target validator is currently connected;
// otherwise we keep the tx local as a best-effort availability heuristic.
func (r *gigaValidatorRouter) EvmProxy(sender common.Address) utils.Option[*ethrpc.Client] {
	if !r.cfg.EnableEvmProxy {
		return utils.None[*ethrpc.Client]()
	}
	validator := r.nextCommitEpoch.Load().Committee().EvmShard(sender)
	if r.validatorKey == validator {
		return utils.None[*ethrpc.Client]()
	}
	if _, ok := r.poolOut.Get(validator); !ok {
		return utils.None[*ethrpc.Client]()
	}
	return r.evmProxy(validator)
}
```

**File:** sei-tendermint/internal/autobahn/producer/mempool.go (L207-278)
```go
func (s *State) insertTx(ctx context.Context, tx tmtypes.Tx, waitIfFull bool) (*abci.ResponseCheckTx, error) {
	if uint64(len(tx)) > types.MaxTxsBytesPerBlock {
		return nil, errTooLarge
	}
	// Reject / wait for a produce session before CheckTxSafe — IsFull and closed
	// are checked after, since they can change while CheckTx runs.
	var mp *mempool
	var err error
	if waitIfFull {
		mp, err = s.getMempool(ctx)
		if err != nil {
			return nil, err
		}
	} else {
		loaded, ok := s.mempool.Load().Get()
		if !ok {
			return nil, ErrNotProducing
		}
		mp = loaded
	}
	resp, err := s.app.CheckTxSafe(ctx, &abci.RequestCheckTxV2{Tx: tx})
	if err != nil {
		return nil, err
	}
	if !resp.IsOK() {
		return resp.ResponseCheckTx, nil
	}
	gasWanted := utils.Clamp[uint64](resp.GasWanted)
	if gasWanted > s.cfg.MaxGasWantedPerBlock {
		return nil, errTooLarge
	}
	// Normalize the gas estimate.
	gasEstimated := utils.Clamp[uint64](resp.GasEstimated)
	if gasEstimated < minTxGas || gasEstimated > gasWanted {
		gasEstimated = gasWanted
	}
	if gasEstimated > s.cfg.MaxGasEstimatedPerBlock {
		return nil, errTooLarge
	}

	for m, ctrl := range mp.inner.Lock() {
		if m.closed {
			return nil, ErrNotProducing
		}
		if m.IsFull() && !waitIfFull {
			return nil, errMempoolFull
		}
		for m.IsFull() {
			// mempool is constructed as a FIFO - we do not delay insertions of large txs (going over cap)
			// in favor of waiting for smaller txs. This simple algorithm allows us to cap
			// pending txs to size of a single block. We can refine this rule later if needed.
			// NOTE: in case there are N concurrent InsertTx calls, this condition is reevaluated N times
			// every time mempool is updated. Depending on proportion of N to the block size it might get too
			// expensive.
			if err := ctrl.Wait(ctx); err != nil {
				return nil, err
			}
			if m.closed {
				return nil, ErrNotProducing
			}
		}
		if resp.IsEVM {
			addr := resp.EVMSenderAddress
			nonce, ok := m.evmNonces[addr]
			if !ok {
				nonce = s.app.EvmNonce(addr)
			}
			if nonce != resp.EVMNonce {
				return nil, fmt.Errorf("%w: got %v, want %v", errBadNonce, resp.EVMNonce, nonce)
			}
			m.evmNonces[addr] = nonce + 1
		}
```
