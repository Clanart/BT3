### Title
Password Parameter Silently Ignored in `personal_sign` and `personal_sendTransaction` — Unauthenticated Signing and Fund Transfer via JSON-RPC (`rpc/namespaces/ethereum/personal/api.go`)

---

### Summary

The `personal_sign` and `personal_sendTransaction` JSON-RPC handlers accept a `password` parameter per the Ethereum `personal_` namespace specification, but both implementations silently discard it (`_ string`). The backend proceeds to sign arbitrary data or broadcast a fully-signed transaction using the node's keyring without ever verifying the caller knows the key's password. Any caller who can reach the `personal` namespace endpoint can sign arbitrary messages or drain any node-managed account without supplying a valid password.

---

### Finding Description

`PrivateAccountAPI.Sign` in `rpc/namespaces/ethereum/personal/api.go` is declared as:

```go
func (api *PrivateAccountAPI) Sign(_ context.Context, data hexutil.Bytes, addr common.Address, _ string) (hexutil.Bytes, error) {
    api.logger.Debug("personal_sign", "data", data, "address", addr.String())
    return api.backend.Sign(addr, data)
}
```

The third positional parameter — the password — is the blank identifier `_ string`. It is never forwarded to the backend. The docstring above the function explicitly states: *"The key used to calculate the signature is decrypted with the given password."* That contract is broken.

`PrivateAccountAPI.SendTransaction` has the same defect:

```go
func (api *PrivateAccountAPI) SendTransaction(_ context.Context, args evmtypes.TransactionArgs, _ string) (common.Hash, error) {
    api.logger.Debug("personal_sendTransaction", "address", args.To.String())
    return api.backend.SendTransaction(args)
}
```

The backend `Backend.Sign` calls `b.clientCtx.Keyring.SignByAddress(from, accounts.TextHash(data), ...)` directly, and `Backend.SendTransaction` calls `msg.Sign(signer, b.clientCtx.Keyring)` directly — both without any password gate.

The `personal` namespace is registered with `Public: false` in `rpc/apis.go`, which in go-ethereum's RPC framework restricts HTTP exposure. However:

1. The namespace is listed in `GetAPINamespaces()` and can be explicitly added to the `api` config field, making it reachable over the configured JSON-RPC address when an operator enables it.
2. Even under IPC-only access, the password bypass means any local process (e.g., a co-located dapp, a compromised sidecar) can sign with or send transactions from any node-managed key without knowing the password — the entire password-based access control layer is absent.

---

### Impact Explanation

`personal_sendTransaction` with the password bypass allows any caller with access to the `personal` namespace to construct a `TransactionArgs` pointing to any node-managed address and have the node sign and broadcast a fund-transfer transaction. This is an unauthorized transfer of EVM-denom funds from node-managed accounts.

`personal_sign` allows the same caller to obtain a valid ECDSA signature over arbitrary data from any node-managed key, which can be used to authorize off-chain operations, sign EIP-712 payloads, or (depending on the dapp) trigger on-chain actions.

---

### Likelihood Explanation

The `personal` namespace is not enabled by default (`GetDefaultAPINamespaces()` returns only `eth`, `net`, `web3`). However, it is a documented, supported namespace that operators enable for development or wallet-node use cases. Once enabled, the password bypass is unconditional — there is no code path that ever checks the supplied password. Any caller who can reach the endpoint (local IPC, or HTTP if the operator exposes it) exploits this with a single JSON-RPC call.

---

### Recommendation

Forward the `password` parameter to the backend and use it to decrypt the key before signing. If the Cosmos SDK keyring backend does not support per-call password decryption (the `TODO` comments in `LockAccount`/`UnlockAccount` reference this limitation), the `personal_sign` and `personal_sendTransaction` methods should return an explicit error rather than silently proceeding without authentication. At minimum, the discarded parameter must not be documented as a security gate while being ignored in the implementation.

---

### Proof of Concept

```
# personal namespace enabled in app.toml: api = "eth,net,web3,personal"
# Node keyring holds address 0xABCD... with password "secret"

curl -X POST http://localhost:8545 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0","method":"personal_sendTransaction",
    "params":[{"from":"0xABCD...","to":"0xATTACKER...","value":"0xDE0B6B3A7640000"},
              "WRONG_PASSWORD_OR_EMPTY"],
    "id":1
  }'
# Returns a transaction hash — funds transferred, password never checked.
```

The password `"WRONG_PASSWORD_OR_EMPTY"` is the blank `_ string` in `PrivateAccountAPI.SendTransaction`; it is never read. The backend signs and broadcasts the transaction unconditionally. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** rpc/namespaces/ethereum/personal/api.go (L145-148)
```go
func (api *PrivateAccountAPI) Sign(_ context.Context, data hexutil.Bytes, addr common.Address, _ string) (hexutil.Bytes, error) {
	api.logger.Debug("personal_sign", "data", data, "address", addr.String())
	return api.backend.Sign(addr, data)
}
```

**File:** rpc/backend/sign_tx.go (L37-79)
```go
// SendTransaction sends transaction based on received args using Node's key to sign it
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
