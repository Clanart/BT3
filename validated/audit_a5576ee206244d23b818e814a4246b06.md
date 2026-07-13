### Title
Cross-Chain Replay via v5 Migration `store.Has()` Bug Silently Enabling `AllowUnprotectedTxs` - (`x/evm/migrations/v5/migrate.go`)

---

### Summary

The v5 store migration reads boolean parameters using `store.Has()` instead of reading and decoding the actual stored value. Because the Cosmos SDK params module always persists all registered keys (including `false` booleans as amino-encoded `0x00` bytes), `store.Has()` returns `true` for any key that was ever set — including `AllowUnprotectedTxs=false`. After migration, `AllowUnprotectedTxs` is silently flipped to `true`. This removes the only ante-handler guard against pre-EIP155 (unprotected) transactions, enabling an unprivileged attacker to replay a legacy transaction signed on any other chain against a victim account on Ethermint.

---

### Finding Description

**Root cause — `store.Has()` misread of boolean params:** [1](#0-0) 

```go
params.EnableCreate        = store.Has(v0types.ParamStoreKeyEnableCreate)
params.EnableCall          = store.Has(v0types.ParamStoreKeyEnableCall)
params.AllowUnprotectedTxs = store.Has(v0types.ParamStoreKeyAllowUnprotectedTxs)
```

The old params module stores every registered parameter key unconditionally, including boolean `false` values (amino-encoded as a single `0x00` byte). `store.Has()` tests only for key existence, not value. Therefore, a chain that had `AllowUnprotectedTxs=false` will have the key present in the store, and after the v5 migration the field is set to `true`.

The correct approach (used for `EvmDenom`) is to `store.Get()` the bytes and decode them: [2](#0-1) 

```go
params.EvmDenom = string(store.Get(v0types.ParamStoreKeyEVMDenom))
```

**Ante-handler guard that is bypassed:** [3](#0-2) 

```go
if !allowUnprotectedTxs && !tx.Protected() {
    return errorsmod.Wrapf(
        errortypes.ErrNotSupported,
        "rejected unprotected Ethereum transaction. Please EIP155 sign your transaction to protect it against replay-attacks")
}
```

When `AllowUnprotectedTxs=true` (incorrectly set by migration), this guard is skipped entirely.

**Signer used for verification:** [4](#0-3) 

```go
ethSigner := ethtypes.MakeSigner(blockCfg.ChainConfig, blockCfg.BlockNumber, blockCfg.BlockTime)
if err := evmante.VerifyEthSig(tx, ethSigner); err != nil {
    return ctx, err
}
```

`ethtypes.MakeSigner` returns a modern signer (e.g., `LondonSigner`), but go-ethereum's `ethtypes.Sender()` falls back to `HomesteadSigner` hash computation for unprotected transactions (V=27/28). The hash does **not** include the chain ID, so a signature produced on Ethereum mainnet (chain-id=1) is cryptographically identical to one produced on Ethermint — the sender is correctly recovered as the victim's address.

**Sender verification:** [5](#0-4) 

```go
func (msg *MsgEthereumTx) VerifySender(signer ethtypes.Signer) error {
    from, err := msg.recoverSender(signer)
    ...
    if !bytes.Equal(msg.From, from.Bytes()) {
        return fmt.Errorf("sender verification failed...")
    }
    return nil
}
```

For an unprotected tx, `recoverSender` recovers the correct victim address — the check passes.

---

### Impact Explanation

An attacker who holds any pre-signed legacy (non-EIP155) Ethereum transaction from another chain can replay it on Ethermint post-migration if the nonce matches. The transaction commits and transfers funds from the victim's account without the victim's consent. This is unauthorized fund theft — **Critical** impact.

---

### Likelihood Explanation

- The v5 migration bug affects **every** Ethermint chain that ran the migration with `AllowUnprotectedTxs=false` (the default: `DefaultAllowUnprotectedTxs = false`). [6](#0-5) 
- The attacker needs only a pre-signed legacy tx from any chain (e.g., Ethereum mainnet pre-EIP155 era, or any chain that still allows unprotected txs) with a matching nonce. Nonce matching is a timing/opportunity condition, not a cryptographic barrier.
- No privileged access, governance, or key compromise is required.

---

### Recommendation

Replace `store.Has()` with proper value decoding for all boolean parameters in the v5 migration:

```go
// Instead of:
params.AllowUnprotectedTxs = store.Has(v0types.ParamStoreKeyAllowUnprotectedTxs)

// Use:
allowUnprotectedBz := store.Get(v0types.ParamStoreKeyAllowUnprotectedTxs)
if len(allowUnprotectedBz) > 0 {
    cdc.MustUnmarshal(allowUnprotectedBz, &someProtoWrapper)
    // or amino-decode the bool
}
```

Apply the same fix to `EnableCreate` and `EnableCall`.

---

### Proof of Concept

1. Deploy Ethermint with `AllowUnprotectedTxs=false` (default).
2. Run the v5 migration (`MigrateStore`). After migration, `AllowUnprotectedTxs=true` due to `store.Has()` returning `true` for the existing key.
3. Obtain any pre-signed legacy (non-EIP155) Ethereum transaction from chain-id=1 that transfers funds from victim address V to attacker address A, with nonce N.
4. Wait until victim's account nonce on Ethermint equals N.
5. Submit the transaction to Ethermint via `eth_sendRawTransaction`.
6. Ante handler: `tx.Protected()=false`, `AllowUnprotectedTxs=true` → guard skipped.
7. `VerifyEthSig`: Homestead-style hash recovers V correctly → passes.
8. Nonce check passes. Transaction commits. Funds transferred from V to A. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** x/evm/migrations/v5/migrate.go (L33-33)
```go
	params.EvmDenom = string(store.Get(v0types.ParamStoreKeyEVMDenom))
```

**File:** x/evm/migrations/v5/migrate.go (L34-47)
```go
	params.EnableCreate = store.Has(v0types.ParamStoreKeyEnableCreate)
	params.EnableCall = store.Has(v0types.ParamStoreKeyEnableCall)
	params.AllowUnprotectedTxs = store.Has(v0types.ParamStoreKeyAllowUnprotectedTxs)
	if err := params.Validate(); err != nil {
		return err
	}
	bz := cdc.MustMarshal(&params)
	store.Set(types.KeyPrefixParams, bz)
	store.Delete(v0types.ParamStoreKeyChainConfig)
	store.Delete(v0types.ParamStoreKeyExtraEIPs)
	store.Delete(v0types.ParamStoreKeyEVMDenom)
	store.Delete(v0types.ParamStoreKeyEnableCreate)
	store.Delete(v0types.ParamStoreKeyEnableCall)
	store.Delete(v0types.ParamStoreKeyAllowUnprotectedTxs)
```

**File:** ante/interfaces/setup.go (L104-130)
```go
	allowUnprotectedTxs := evmParams.GetAllowUnprotectedTxs()

	for _, msg := range protoTx.GetMsgs() {
		msgEthTx, ok := msg.(*evmtypes.MsgEthereumTx)
		if !ok {
			return errorsmod.Wrapf(errortypes.ErrUnknownRequest, "invalid message type %T, expected %T", msg, (*evmtypes.MsgEthereumTx)(nil))
		}

		txGasLimit += msgEthTx.GetGas()

		tx := msgEthTx.AsTransaction()
		// return error if contract creation or call are disabled through governance
		if !enableCreate && tx.To() == nil {
			return errorsmod.Wrap(evmtypes.ErrCreateDisabled, "failed to create new contract")
		} else if !enableCall && tx.To() != nil {
			return errorsmod.Wrap(evmtypes.ErrCallDisabled, "failed to call contract")
		}

		if baseFee == nil && tx.Type() == ethtypes.DynamicFeeTxType {
			return errorsmod.Wrap(ethtypes.ErrTxTypeNotSupported, "dynamic fee tx not supported")
		}

		if !allowUnprotectedTxs && !tx.Protected() {
			return errorsmod.Wrapf(
				errortypes.ErrNotSupported,
				"rejected unprotected Ethereum transaction. Please EIP155 sign your transaction to protect it against replay-attacks")
		}
```

**File:** evmd/ante/handler_options.go (L118-125)
```go
		if err := interfaces.ValidateEthBasic(ctx, tx, evmParams, baseFee); err != nil {
			return ctx, err
		}

		ethSigner := ethtypes.MakeSigner(blockCfg.ChainConfig, blockCfg.BlockNumber, blockCfg.BlockTime)
		if err := evmante.VerifyEthSig(tx, ethSigner); err != nil {
			return ctx, err
		}
```

**File:** x/evm/types/msg.go (L337-346)
```go
func (msg *MsgEthereumTx) VerifySender(signer ethtypes.Signer) error {
	from, err := msg.recoverSender(signer)
	if err != nil {
		return err
	}

	if !bytes.Equal(msg.From, from.Bytes()) {
		return fmt.Errorf("sender verification failed. got %s, expected %s", HexAddress(from.Bytes()), HexAddress(msg.From))
	}
	return nil
```

**File:** x/evm/types/params.go (L33-33)
```go
	DefaultAllowUnprotectedTxs = false
```
