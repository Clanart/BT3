### Title
Password Authentication Silently Bypassed in `personal_sign` and `personal_sendTransaction` — (File: `rpc/namespaces/ethereum/personal/api.go`)

### Summary
The `personal_sign` and `personal_sendTransaction` JSON-RPC methods accept a password parameter per the Ethereum `personal_` API specification — the password is the sole access-control gate that is supposed to decrypt and authorize use of the node's keyring key. In Ethermint both parameters are silently discarded (`_ string`), so any caller who can reach the JSON-RPC endpoint when the `personal` namespace is enabled can sign arbitrary messages or broadcast transactions from any account held in the node's keyring without supplying the correct password.

### Finding Description

The Ethereum `personal_` API specification requires the caller to supply the account password to authorize key use. In Ethermint's implementation both methods discard the password entirely:

`rpc/namespaces/ethereum/personal/api.go` line 131 (`SendTransaction`):
```go
func (api *PrivateAccountAPI) SendTransaction(_ context.Context, args evmtypes.TransactionArgs, _ string) (common.Hash, error) {
    api.logger.Debug("personal_sendTransaction", "address", args.To.String())
    return api.backend.SendTransaction(args)   // password never forwarded
}
``` [1](#0-0) 

`rpc/namespaces/ethereum/personal/api.go` line 145 (`Sign`):
```go
// The key used to calculate the signature is decrypted with the given password.
func (api *PrivateAccountAPI) Sign(_ context.Context, data hexutil.Bytes, addr common.Address, _ string) (hexutil.Bytes, error) {
    api.logger.Debug("personal_sign", "data", data, "address", addr.String())
    return api.backend.Sign(addr, data)        // password never forwarded
}
``` [2](#0-1) 

The backend `Backend.SendTransaction` calls `msg.Sign(signer, b.clientCtx.Keyring)` and `Backend.Sign` calls `b.clientCtx.Keyring.SignByAddress(...)` — neither receives nor checks any password: [3](#0-2) [4](#0-3) 

The `personal` namespace is not in the default API list (`["eth","net","web3"]`), but it is a documented, supported option: [5](#0-4) 

When enabled, it is registered over HTTP with `rpcServer.RegisterName`: [6](#0-5) 

The `Public: false` field is metadata only; it does not prevent HTTP access once the namespace is registered.

### Impact Explanation

When the `personal` namespace is enabled, any unauthenticated HTTP caller can:

1. **Send transactions from any keyring account** via `personal_sendTransaction` — including value transfers and contract calls — without knowing the account password. This constitutes unauthorized fund transfer from the node operator's accounts.
2. **Sign arbitrary messages** via `personal_sign` from any keyring account, enabling off-chain authorization forgery (e.g., EIP-712 permit signatures, bridge authorizations, DAO votes).

This is a signer-verification bypass: the password that is the only intended access-control gate is silently dropped, leaving the keyring fully open to any network-reachable caller.

### Likelihood Explanation

The `personal` namespace must be explicitly added to the `api` config list. However, it is a well-known, documented namespace that operators commonly enable for Ethereum tooling compatibility (MetaMask, Hardhat, Foundry). Once enabled, the attack requires only a single unauthenticated JSON-RPC POST to the node's HTTP endpoint — no special privileges, no on-chain state, no prior knowledge beyond the target address.

### Recommendation

- Forward the password parameter to the keyring and use it to decrypt/authorize the key before signing. For the Cosmos SDK keyring `file` backend this means calling `ExportPrivKeyArmor` + `UnarmorDecryptPrivKey` with the supplied password, then signing with the decrypted key.
- Until a proper password-gated path is implemented, return an explicit `not supported` error from `Sign` and `SendTransaction` rather than silently ignoring the password, so callers are not misled into believing the password was verified.
- Consider gating the `personal` namespace behind an explicit opt-in warning in documentation, similar to how `unsafe-export-eth-key` is labeled.

### Proof of Concept

Assuming the node is started with `--json-rpc.api eth,net,web3,personal` and the keyring holds an account at `0xABCD...`:

```bash
# personal_sendTransaction — wrong password accepted, tx broadcast succeeds
curl -X POST http://localhost:8545 \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc":"2.0","method":"personal_sendTransaction","id":1,
    "params":[{"from":"0xABCD...","to":"0xDEAD...","value":"0xDE0B6B3A7640000"},
              "WRONG_PASSWORD_IGNORED"]
  }'

# personal_sign — wrong password accepted, signature returned
curl -X POST http://localhost:8545 \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc":"2.0","method":"personal_sign","id":2,
    "params":["0xdeadbeef","0xABCD...","WRONG_PASSWORD_IGNORED"]
  }'
```

Both calls succeed regardless of the password supplied, because the password field is discarded at `rpc/namespaces/ethereum/personal/api.go` lines 131 and 145 before reaching the backend. [7](#0-6) [8](#0-7)

### Citations

**File:** rpc/namespaces/ethereum/personal/api.go (L128-134)
```go
// SendTransaction will create a transaction from the given arguments and
// tries to sign it with the key associated with args.To. If the given password isn't
// able to decrypt the key it fails.
func (api *PrivateAccountAPI) SendTransaction(_ context.Context, args evmtypes.TransactionArgs, _ string) (common.Hash, error) {
	api.logger.Debug("personal_sendTransaction", "address", args.To.String())
	return api.backend.SendTransaction(args)
}
```

**File:** rpc/namespaces/ethereum/personal/api.go (L136-148)
```go
// Sign calculates an Ethereum ECDSA signature for:
// keccak256("\x19Ethereum Signed Message:\n" + len(message) + message))
//
// Note, the produced signature conforms to the secp256k1 curve R, S and V values,
// where the V value will be 27 or 28 for legacy reasons.
//
// The key used to calculate the signature is decrypted with the given password.
//
// https://github.com/ethereum/go-ethereum/wiki/Management-APIs#personal_sign
func (api *PrivateAccountAPI) Sign(_ context.Context, data hexutil.Bytes, addr common.Address, _ string) (hexutil.Bytes, error) {
	api.logger.Debug("personal_sign", "data", data, "address", addr.String())
	return api.backend.Sign(addr, data)
}
```

**File:** rpc/backend/sign_tx.go (L38-79)
```go
func (b *Backend) SendTransaction(args evmtypes.TransactionArgs) (common.Hash, error) {
	// Look up the wallet containing the requested signer
	_, err := b.clientCtx.Keyring.KeyByAddress(sdk.AccAddress(args.GetFrom().Bytes()))
	if err != nil {
		b.logger.Error("failed to find key in keyring", "address", args.GetFrom(), "error", err.Error())
		return common.Hash{}, fmt.Errorf("failed to find key in the node's keyring; %s; %s", keystore.ErrNoMatch, err.Error())
	}

	if args.ChainID != nil && (b.chainID).Cmp((*big.Int)(args.ChainID)) != 0 {
		return common.Hash{}, fmt.Errorf("chainId does not match node's (have=%v, want=%v)", args.ChainID, (*hexutil.Big)(b.chainID))
	}

	args, err = b.SetTxDefaults(args)
	if err != nil {
		return common.Hash{}, err
	}

	msg := args.ToTransaction()
	if err := msg.ValidateBasic(); err != nil {
		b.logger.Debug("tx failed basic validation", "error", err.Error())
		return common.Hash{}, err
	}

	bn, err := b.BlockNumber()
	if err != nil {
		b.logger.Debug("failed to fetch latest block number", "error", err.Error())
		return common.Hash{}, err
	}

	header, err := b.CurrentHeader()
	if err != nil {
		b.logger.Debug("failed to fetch latest block header", "error", err.Error())
		return common.Hash{}, err
	}

	signer := ethtypes.MakeSigner(b.ChainConfig(), new(big.Int).SetUint64(uint64(bn)), header.Time)

	// Sign transaction
	if err := msg.Sign(signer, b.clientCtx.Keyring); err != nil {
		b.logger.Debug("failed to sign tx", "error", err.Error())
		return common.Hash{}, err
	}
```

**File:** rpc/backend/sign_tx.go (L128-148)
```go
// Sign signs the provided data using the private key of address via Geth's signature standard.
func (b *Backend) Sign(address common.Address, data hexutil.Bytes) (hexutil.Bytes, error) {
	from := sdk.AccAddress(address.Bytes())

	_, err := b.clientCtx.Keyring.KeyByAddress(from)
	if err != nil {
		b.logger.Error("failed to find key in keyring", "address", address.String())
		return nil, fmt.Errorf("%s; %s", keystore.ErrNoMatch, err.Error())
	}

	// Apply EIP-191 signed-message prefix to domain-separate personal
	// signatures from transaction signatures (matching Geth's eth_sign).
	signature, _, err := b.clientCtx.Keyring.SignByAddress(from, accounts.TextHash(data), signingtypes.SignMode_SIGN_MODE_TEXTUAL)
	if err != nil {
		b.logger.Error("keyring.SignByAddress failed", "address", address.Hex())
		return nil, err
	}

	signature[crypto.RecoveryIDOffset] += 27 // Transform V from 0/1 to 27/28 according to the yellow paper
	return signature, nil
}
```

**File:** server/config/config.go (L251-258)
```go
// GetDefaultAPINamespaces returns the default list of JSON-RPC namespaces that should be enabled
func GetDefaultAPINamespaces() []string {
	return []string{"eth", "net", "web3"}
}

// GetAPINamespaces returns the all the available JSON-RPC API namespaces.
func GetAPINamespaces() []string {
	return []string{"web3", "eth", "personal", "net", "txpool", "debug", "miner"}
```

**File:** rpc/apis.go (L128-144)
```go
		PersonalNamespace: func(ctx *server.Context,
			clientCtx client.Context,
			_ *stream.RPCStream,
			allowUnprotectedTxs bool,
			indexer ethermint.EVMTxIndexer,
			mempoolClient appmempool.MempoolClient,
		) []rpc.API {
			evmBackend := backend.NewBackend(ctx, ctx.Logger, clientCtx, allowUnprotectedTxs, indexer, backend.WithMempoolClient(mempoolClient))
			return []rpc.API{
				{
					Namespace: PersonalNamespace,
					Version:   apiVersion,
					Service:   personal.NewAPI(ctx.Logger, evmBackend),
					Public:    false,
				},
			}
		},
```
