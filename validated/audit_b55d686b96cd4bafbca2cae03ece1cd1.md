### Title
Unbounded Loop Over Attacker-Controlled `req.Txs` Array in `TraceBlock` gRPC Handler Enables Node-Level DoS - (File: x/evm/keeper/grpc_query.go)

### Summary

The `TraceBlock` gRPC handler iterates over an unbounded, caller-supplied `req.Txs` array, executing a full EVM state transition (`prepareTrace` → `ApplyMessageWithConfig`) for every entry. No length cap is enforced before the loop. An unprivileged caller can submit a single gRPC or REST request containing thousands of complex EVM transactions and force the node to perform unbounded EVM execution, exhausting CPU and memory and causing the node to crash or become unresponsive.

### Finding Description

`Keeper.TraceBlock` in `x/evm/keeper/grpc_query.go` accepts a `QueryTraceBlockRequest` whose `Txs` field is a `repeated MsgEthereumTx` protobuf array with no declared size limit. [1](#0-0) 

The handler performs only a single guard — a check that `TraceConfig.Limit` is non-negative — before entering the loop: [2](#0-1) 

It then iterates over every element of `req.Txs` without any bound check: [3](#0-2) 

For each element, `k.prepareTrace()` is called, which internally calls `ApplyMessageWithConfig` — a full EVM state transition including signature recovery, intrinsic gas calculation, access-list preparation, and EVM opcode execution: [4](#0-3) 

The same pattern exists in `TraceTx`, where `req.Predecessors` is an unbounded `[]*MsgEthereumTx` array iterated without a length check, each entry triggering `k.ApplyMessageWithConfig` with `commit=true`: [5](#0-4) 

The `QueryTraceBlockRequest.Txs` field is defined as a plain `repeated` field in the proto definition with no `(validate.rules)` or size annotation: [6](#0-5) 

The endpoint is reachable via both the gRPC server and the REST gateway: [7](#0-6) 

### Impact Explanation

An unprivileged caller sends one `TraceBlock` gRPC (or REST `GET /ethermint/evm/v1/trace_block`) request containing N arbitrarily complex `MsgEthereumTx` entries. The node executes N full EVM state transitions synchronously in the gRPC handler goroutine. With N in the tens of thousands and each transaction containing large calldata or deep call stacks, this exhausts CPU and heap memory. The node process OOMs or becomes unresponsive. If the targeted node is a validator, it misses block proposals and pre-commits, contributing to chain liveness failure. This matches the allowed High impact: "Public JSON-RPC, gRPC, simulation, tracing, receipt/log, or indexer path… exposes a reachable route to the impacts above" (chain halt).

### Likelihood Explanation

The gRPC and REST endpoints are publicly exposed on standard Ethermint nodes. No authentication, rate-limiting, or size validation is applied at the handler level. A single HTTP/2 request is sufficient to trigger the condition. The attack requires no on-chain funds, no private keys, and no prior state. Likelihood is **High**.

### Recommendation

1. Add an explicit maximum length check on `req.Txs` (and `req.Predecessors` in `TraceTx`) before entering the loop, returning `codes.InvalidArgument` if the limit is exceeded. A reasonable cap is the maximum transactions per block (e.g., `MaxTxsPerBlock` or a configurable `queryMaxTxs` parameter analogous to the existing `queryMaxGasLimit`).
2. Apply the same cap in `TraceTx` for `req.Predecessors`.
3. Consider enforcing a per-request gas budget across all predecessor/block-tx executions so that even within the allowed count, total computation is bounded.

```go
// Example guard in TraceBlock
const maxTraceBlockTxs = 1000
if len(req.Txs) > maxTraceBlockTxs {
    return nil, status.Errorf(codes.InvalidArgument,
        "too many txs in trace request: %d > %d", len(req.Txs), maxTraceBlockTxs)
}
```

### Proof of Concept

```python
import grpc
from ethermint.evm.v1 import query_pb2, query_pb2_grpc, tx_pb2

channel = grpc.insecure_channel("validator-node:9090")
stub = query_pb2_grpc.QueryStub(channel)

# Build a minimal but valid MsgEthereumTx (e.g., a large-calldata CALL)
evil_tx = tx_pb2.MsgEthereumTx(...)  # populate with valid fields

# Flood the Txs array with 50,000 entries
req = query_pb2.QueryTraceBlockRequest(
    txs=[evil_tx] * 50_000,
    block_number=1,
    chain_id=9000,
)

# Single unauthenticated request triggers 50,000 full EVM executions
stub.TraceBlock(req)  # node OOMs / becomes unresponsive
```

The `TraceBlock` handler will iterate all 50,000 entries, calling `prepareTrace` → `ApplyMessageWithConfig` for each, exhausting node resources. [8](#0-7)

### Citations

**File:** x/evm/types/query.pb.go (L1343-1358)
```go
type QueryTraceBlockRequest struct {
	// txs is an array of messages in the block
	Txs []*MsgEthereumTx `protobuf:"bytes,1,rep,name=txs,proto3" json:"txs,omitempty"`
	// trace_config holds extra parameters to trace functions.
	TraceConfig *TraceConfig `protobuf:"bytes,3,opt,name=trace_config,json=traceConfig,proto3" json:"trace_config,omitempty"`
	// block_number of the traced block
	BlockNumber int64 `protobuf:"varint,5,opt,name=block_number,json=blockNumber,proto3" json:"block_number,omitempty"`
	// block_hash (hex) of the traced block
	BlockHash string `protobuf:"bytes,6,opt,name=block_hash,json=blockHash,proto3" json:"block_hash,omitempty"`
	// block_time of the traced block
	BlockTime time.Time `protobuf:"bytes,7,opt,name=block_time,json=blockTime,proto3,stdtime" json:"block_time"`
	// proposer_address is the address of the requested block
	ProposerAddress github_com_cosmos_cosmos_sdk_types.ConsAddress `protobuf:"bytes,8,opt,name=proposer_address,json=proposerAddress,proto3,casttype=github.com/cosmos/cosmos-sdk/types.ConsAddress" json:"proposer_address,omitempty"`
	// chain_id is the eip155 chain id parsed from the requested block header
	ChainId int64 `protobuf:"varint,9,opt,name=chain_id,json=chainId,proto3" json:"chain_id,omitempty"`
}
```

**File:** x/evm/keeper/grpc_query.go (L542-564)
```go
			for i, tx := range req.Predecessors {
				ethTx := tx.AsTransaction()
				msg, err := core.TransactionToMessage(ethTx, signer, cfg.BaseFee)
				if err != nil {
					k.Logger(ctx).Debug("trace: skipping predecessor, failed to convert tx to message",
						"index", i, "hash", ethTx.Hash().Hex(), "err", err.Error())
					continue
				}
				cfg.TxConfig.TxHash = ethTx.Hash()
				cfg.TxConfig.TxIndex, err = ethermint.SafeUint(i)
				if err != nil {
					k.Logger(ctx).Debug("trace: skipping predecessor, invalid tx index",
						"index", i, "hash", ethTx.Hash().Hex(), "err", err.Error())
					continue
				}
				rsp, err := k.ApplyMessageWithConfig(ctx, msg, cfg, true)
				if err != nil {
					k.Logger(ctx).Error("trace: predecessor replay failed, trace state may be incomplete",
						"index", i, "hash", ethTx.Hash().Hex(), "err", err.Error())
					continue
				}
				cfg.TxConfig.LogIndex += uint(len(rsp.Logs))
			}
```

**File:** x/evm/keeper/grpc_query.go (L587-644)
```go
func (k Keeper) TraceBlock(c context.Context, req *types.QueryTraceBlockRequest) (*types.QueryTraceBlockResponse, error) {
	if req == nil {
		return nil, status.Error(codes.InvalidArgument, "empty request")
	}

	if req.TraceConfig != nil && req.TraceConfig.Limit < 0 {
		return nil, status.Errorf(codes.InvalidArgument, "output limit cannot be negative, got %d", req.TraceConfig.Limit)
	}

	// get the context of block beginning
	contextHeight := req.BlockNumber
	if contextHeight < 1 {
		// 0 is a special value in `ContextWithHeight`
		contextHeight = 1
	}

	ctx := sdk.UnwrapSDKContext(c)
	ctx = ctx.WithBlockHeight(contextHeight)
	ctx = ctx.WithBlockTime(req.BlockTime)
	ctx = ctx.WithHeaderHash(common.Hex2Bytes(req.BlockHash))
	ctx = ctx.WithProposer(GetProposerAddress(ctx, req.ProposerAddress))
	chainID, err := getChainID(ctx, req.ChainId)
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, err.Error())
	}

	cfg, err := k.EVMConfig(ctx, chainID, common.Hash{})
	if err != nil {
		return nil, status.Error(codes.Internal, "failed to load evm config")
	}
	cfg.TraceReplay = req.TraceConfig.GetTraceReplay()
	signer := ethtypes.MakeSigner(cfg.ChainConfig, big.NewInt(ctx.BlockHeight()), uint64(ctx.BlockTime().Unix())) //#nosec G115
	txsLength := len(req.Txs)
	results := make([]*types.TxTraceResult, 0, txsLength)

	for i, tx := range req.Txs {
		result := types.TxTraceResult{}
		ethTx := tx.AsTransaction()
		cfg.TxConfig.TxHash = ethTx.Hash()
		result.TxHash = ethTx.Hash()
		cfg.TxConfig.TxIndex, err = ethermint.SafeUint(i)
		if err != nil {
			return nil, err
		}
		msg, err := core.TransactionToMessage(ethTx, signer, cfg.BaseFee)
		if err != nil {
			result.Error = status.Error(codes.Internal, err.Error()).Error()
		} else {
			traceResult, logIndex, err := k.prepareTrace(ctx, cfg, msg, req.TraceConfig, true)
			if err != nil {
				result.Error = err.Error()
			} else {
				cfg.TxConfig.LogIndex = logIndex
				result.Result = traceResult
			}
		}
		results = append(results, &result)
	}
```

**File:** x/evm/keeper/state_transition.go (L423-438)
```go
	}

	rules := cfg.Rules
	contractCreation := msg.To == nil
	intrinsicGas, err := k.GetEthIntrinsicGas(msg, rules, contractCreation)
	if err != nil {
		// should have already been checked on Ante Handler
		return nil, errorsmod.Wrap(err, "intrinsic gas failed")
	}

	// Should check again even if it is checked on Ante Handler, because eth_call don't go through Ante Handler.
	if leftoverGas < intrinsicGas {
		// eth_estimateGas will check for this exact error
		return nil, errorsmod.Wrap(core.ErrIntrinsicGas, "apply message")
	}
	leftoverGas -= intrinsicGas
```

**File:** proto/ethermint/evm/v1/query.proto (L280-304)
```text
// QueryTraceTxRequest defines TraceTx request
message QueryTraceTxRequest {
  // msg is the MsgEthereumTx for the requested transaction
  MsgEthereumTx msg = 1;
  // tx_index is not necessary anymore
  reserved 2;
  reserved "tx_index";
  // trace_config holds extra parameters to trace functions.
  TraceConfig trace_config = 3;
  // predecessors is an array of transactions included in the same block
  // need to be replayed first to get correct context for tracing.
  repeated MsgEthereumTx predecessors = 4;
  // block_number of requested transaction
  int64 block_number = 5;
  // block_hash of requested transaction
  string block_hash = 6;
  // block_time of requested transaction
  google.protobuf.Timestamp block_time = 7 [(gogoproto.nullable) = false, (gogoproto.stdtime) = true];
  // proposer_address is the proposer of the requested block
  bytes proposer_address = 8 [(gogoproto.casttype) = "github.com/cosmos/cosmos-sdk/types.ConsAddress"];
  // chain_id is the eip155 chain id parsed from the requested block header
  int64 chain_id = 9;
  // base_fee is the base fee based on the block_number of requested transaction
  string base_fee = 10 [(gogoproto.customtype) = "cosmossdk.io/math.Int"];
}
```

**File:** x/evm/types/query.pb.gw.go (L904-925)
```go
	mux.Handle("GET", pattern_Query_TraceBlock_0, func(w http.ResponseWriter, req *http.Request, pathParams map[string]string) {
		ctx, cancel := context.WithCancel(req.Context())
		defer cancel()
		var stream runtime.ServerTransportStream
		ctx = grpc.NewContextWithServerTransportStream(ctx, &stream)
		inboundMarshaler, outboundMarshaler := runtime.MarshalerForRequest(mux, req)
		rctx, err := runtime.AnnotateIncomingContext(ctx, mux, req)
		if err != nil {
			runtime.HTTPError(ctx, mux, outboundMarshaler, w, req, err)
			return
		}
		resp, md, err := local_request_Query_TraceBlock_0(rctx, inboundMarshaler, server, req, pathParams)
		md.HeaderMD, md.TrailerMD = metadata.Join(md.HeaderMD, stream.Header()), metadata.Join(md.TrailerMD, stream.Trailer())
		ctx = runtime.NewServerMetadataContext(ctx, md)
		if err != nil {
			runtime.HTTPError(ctx, mux, outboundMarshaler, w, req, err)
			return
		}

		forward_Query_TraceBlock_0(ctx, mux, outboundMarshaler, w, req, resp, mux.GetForwardResponseOptions()...)

	})
```
