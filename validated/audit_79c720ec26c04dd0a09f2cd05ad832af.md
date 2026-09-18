## Title
PREVRANDAO (opcode `0x44`) Derived from Predictable Block Timestamp Hash - ([File: x/evm/keeper/keeper.go])

### Summary
The Sei EVM's `GetVMBlockContext` sets the `Random` field of the `vm.BlockContext` (which backs the `PREVRANDAO`/`DIFFICULTY` opcode `0x44`) to `Keccak256Hash(blockTimestamp)` rather than to a value with full 256-bit entropy. Because the block timestamp is a low-entropy, monotonically increasing, and externally predictable/observable quantity, the resulting "randomness" is heavily biased and predictable, matching the exact bug class described in the external report (Otter Audits finding, fixed upstream via PR #1740).

### Finding Description
In `x/evm/keeper/keeper.go`, `GetVMBlockContext` computes the value used for `PREVRANDAO` as: [1](#0-0) 

```go
// Use hash of block timestamp as info for PREVRANDAO
r, err := ctx.BlockHeader().Time.MarshalBinary()
...
rh := crypto.Keccak256Hash(r)
...
return &vm.BlockContext{
    ...
    Difficulty:  utils.Big0,                            // only needed for PoW
    ...
    Random:      &rh,
}, nil
```

The identical pattern is duplicated in the `giga` execution path at [2](#0-1) .

The `vm.BlockContext.Random` value is what geth's interpreter returns for the `RANDOM`/`DIFFICULTY` opcode (`0x44`), which Solidity exposes as `block.prevrandao` (also demonstrated by the test contract's usage: `prevrandao = block.prevrandao;` and `keccak256(abi.encodePacked(block.number, block.prevrandao, counter))`) [3](#0-2) .

The core issue is that a Cosmos block `Time` (a Unix timestamp with, at best, second or millisecond granularity) has vastly less entropy than the 256 bits expected by consumers of `PREVRANDAO`. Hashing a low-entropy, externally-known input with Keccak256 does not add entropy — it only obfuscates it superficially. Since block timestamps:
- increase predictably (roughly monotonic, bounded by expected block time),
- are known/observable by any RPC client before the transaction executing in that block is included, and
- are indirectly influenced by validators/proposers (who choose the block time, subject to consensus rules),

the resulting `Random` value is fully predictable ahead of time by anyone able to guess or observe the timestamp that will be used for the target block. This lets an attacker precompute `Keccak256Hash(timestamp)` for the (small) set of plausible upcoming timestamps and choose transactions/moves accordingly, or a block proposer with any timestamp latitude to bias outcomes — exactly the bias/predictability pattern (constant upper bytes, colliding low-order bits after modulo operations across nearby timestamps) described in the report.

By contrast, the replay/testing code paths in the same file correctly source `Random` from the real block header's `MixDigest` (full-entropy PoS randao value) when replaying real Ethereum blocks: [4](#0-3) . This confirms the codebase is aware that `Random` should carry high-entropy randomness, but the primary Sei EVM block-context path substitutes a predictable timestamp hash instead.

### Impact Explanation
Any Solidity/EVM contract deployed on Sei that relies on `block.prevrandao` (or inline-assembly `difficulty()`/`prevrandao()`) for randomness — e.g., lottery, gaming, NFT trait/rarity assignment, auction tie-breaking, or any contract using it as a commit-less randomness source — can have its outcome predicted or biased by any unprivileged actor who can observe or influence the block timestamp of the target block. This can lead to unauthorized/unfair extraction of value from such contracts (e.g., predicting or steering "random" game/lottery outcomes), which constitutes fund loss for affected contract users, matching the accepted impact categories (unauthorized transfer / fund loss via predictable EVM randomness exploited through ordinary transactions).

### Likelihood Explanation
Any EVM contract deployer or user can trivially exploit this from an ordinary transaction — no privileged access, precompile, or special permission is required. The attack only requires knowledge of (or the ability to narrow down) the upcoming block timestamp, which is either directly observable via the RPC (`eth_getBlockByNumber`, pending block info) or bounded within Sei's known target block time. This is a straightforward and repeatable exploitation path for any contract that naively uses `PREVRANDAO` as a randomness source, which is a common (if discouraged) pattern ported directly from Ethereum L1/L2 contracts being deployed to Sei's EVM.

### Recommendation
Replace the `Keccak256Hash(blockTimestamp)` construction in `GetVMBlockContext` (both `x/evm/keeper/keeper.go` and `giga/deps/xevm/keeper/keeper.go`) with a high-entropy, unpredictable-until-block-finalization value, and clearly document Sei's specific PREVRANDAO semantics (as other EVM-compatible chains do) so integrators are not misled into treating it as cryptographically secure randomness. Candidate entropy sources already available in `sdk.Context`/Tendermint block data include the block's proposer-independent hash/commit signatures (`ctx.HeaderHash()`, validator aggregated signatures) rather than the self-selected, low-entropy timestamp field.

### Proof of Concept
1. Deploy a contract that uses `block.prevrandao` for a "random" decision, e.g. the provided `EVMCompatibilityTester.getBlockProperties()`/`useGas()` pattern combining `block.number` and `block.prevrandao` [3](#0-2) .
2. Off-chain, compute `Keccak256Hash(marshalBinary(T))` for the range of plausible timestamps `T` the target block will carry (bounded by Sei's known block interval and the current time), reproducing exactly the logic in `GetVMBlockContext`: [5](#0-4) .
3. Because the set of plausible `T` is small and each candidate reduces to a hash the attacker can precompute, the attacker can determine (or, if a block proposer, choose) the exact `PREVRANDAO` value before submitting a transaction, and act only when the derived value favors them — defeating the intended randomness guarantee.

### Citations

**File:** x/evm/keeper/keeper.go (L275-313)
```go
	// Use hash of block timestamp as info for PREVRANDAO
	r, err := ctx.BlockHeader().Time.MarshalBinary()
	if err != nil {
		return nil, err
	}
	rh := crypto.Keccak256Hash(r)

	txfer := func(db vm.StateDB, sender, recipient common.Address, amount *uint256.Int) {
		if IsPayablePrecompile(&recipient) {
			state.TransferWithoutEvents(db, sender, recipient, amount)
		} else {
			core.Transfer(db, sender, recipient, amount)
		}
	}
	var baseFee *big.Int
	if ctx.ChainID() == Pacific1ChainID && ctx.BlockHeight() < 114945913 {
		baseFee = k.GetBaseFeePerGas(ctx).TruncateInt().BigInt()
	} else {
		baseFee = k.GetNextBaseFeePerGas(ctx).TruncateInt().BigInt()
	}

	return &vm.BlockContext{
		CanTransfer: core.CanTransfer,
		Transfer:    txfer,
		GetHash:     k.GetHashFn(ctx),
		Coinbase:    coinbase,
		GasLimit: func() uint64 {
			if ctx.ConsensusParams() != nil && ctx.ConsensusParams().Block != nil {
				return uint64(ctx.ConsensusParams().Block.MaxGas) //nolint:gosec
			}
			return DefaultBlockGasLimit
		}(),
		BlockNumber: big.NewInt(ctx.BlockHeight()),
		Time:        uint64(ctx.BlockHeader().Time.Unix()), //nolint:gosec
		Difficulty:  utils.Big0,                            // only needed for PoW
		BaseFee:     baseFee,
		BlobBaseFee: utils.Big1, // Cancun not enabled
		Random:      &rh,
	}, nil
```

**File:** x/evm/keeper/keeper.go (L500-534)
```go
func (k *Keeper) getReplayBlockCtx(ctx sdk.Context) (*vm.BlockContext, error) {
	header := k.ReplayBlock.Header_
	replayCtx := &ReplayChainContext{ethClient: k.EthClient, chainID: k.ChainID(ctx), params: k.GetParams(ctx)}
	getHash := core.GetHashFn(header, replayCtx)
	var (
		baseFee     *big.Int
		blobBaseFee *big.Int
		random      *common.Hash
	)
	if header.BaseFee != nil {
		baseFee = new(big.Int).Set(header.BaseFee)
	} else {
		baseFee = big.NewInt(0)
	}
	if header.ExcessBlobGas != nil {
		blobBaseFee = eip4844.CalcBlobFee(replayCtx.Config(), header)
	} else {
		blobBaseFee = big.NewInt(0)
	}
	if header.Difficulty.Cmp(common.Big0) == 0 {
		random = &header.MixDigest
	}
	return &vm.BlockContext{
		CanTransfer: core.CanTransfer,
		Transfer:    core.Transfer,
		GetHash:     getHash,
		Coinbase:    header.Coinbase,
		GasLimit:    header.GasLimit,
		BlockNumber: new(big.Int).Set(header.Number),
		Time:        header.Time,
		Difficulty:  new(big.Int).Set(header.Difficulty),
		BaseFee:     baseFee,
		BlobBaseFee: blobBaseFee,
		Random:      random,
	}, nil
```

**File:** giga/deps/xevm/keeper/keeper.go (L259-297)
```go
	// Use hash of block timestamp as info for PREVRANDAO
	r, err := ctx.BlockHeader().Time.MarshalBinary()
	if err != nil {
		return nil, err
	}
	rh := crypto.Keccak256Hash(r)

	txfer := func(db vm.StateDB, sender, recipient common.Address, amount *uint256.Int) {
		if IsPayablePrecompile(&recipient) {
			state.TransferWithoutEvents(db, sender, recipient, amount)
		} else {
			core.Transfer(db, sender, recipient, amount)
		}
	}
	var baseFee *big.Int
	if ctx.ChainID() == Pacific1ChainID && ctx.BlockHeight() < 114945913 {
		baseFee = k.GetBaseFeePerGas(ctx).TruncateInt().BigInt()
	} else {
		baseFee = k.GetNextBaseFeePerGas(ctx).TruncateInt().BigInt()
	}

	return &vm.BlockContext{
		CanTransfer: core.CanTransfer,
		Transfer:    txfer,
		GetHash:     k.GetHashFn(ctx),
		Coinbase:    coinbase,
		GasLimit: func() uint64 {
			if ctx.ConsensusParams() != nil && ctx.ConsensusParams().Block != nil {
				return uint64(ctx.ConsensusParams().Block.MaxGas) //nolint:gosec
			}
			return DefaultBlockGasLimit
		}(),
		BlockNumber: big.NewInt(ctx.BlockHeight()),
		Time:        uint64(ctx.BlockHeader().Time.Unix()), //nolint:gosec
		Difficulty:  utils.Big0,                            // only needed for PoW
		BaseFee:     baseFee,
		BlobBaseFee: utils.Big1, // Cancun not enabled
		Random:      &rh,
	}, nil
```

**File:** contracts/src/EVMCompatibilityTester.sol (L119-128)
```text
    function getBlockProperties() public view returns (bytes32 blockHash, address payable coinbase, uint prevrandao, uint gaslimit, uint number, uint timestamp) {
        blockHash = blockhash(block.number - 1);
        coinbase = block.coinbase;
        prevrandao = block.prevrandao;
        gaslimit = block.gaslimit;
        number = block.number;
        timestamp = block.timestamp;

        return (blockHash, coinbase, prevrandao, gaslimit, number, timestamp);
    }
```
