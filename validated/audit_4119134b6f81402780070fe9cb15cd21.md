## Cross-chain Replay in `associate()` — customMessageHash omits chain ID (Analog to reported H-01)

### Title
Cross-chain Replay of `associate()` Signatures Forces Unintended Address Association and Balance Migration - (File: `precompiles/addr/addr.go`)

### Summary
The `addr` precompile's `associate()` function (and all its legacy versioned copies) recovers an EVM address/Sei address/pubkey from a signature over a user-supplied `customMessage`, but the signed hash is computed as a bare `keccak256(customMessage)` with **no chain ID or other domain separator** baked in. Because Sei runs multiple independent chains (`pacific-1`, `atlantic-2`, `arctic-1`, and various testnets/devnets) sharing the same address-derivation and association logic, a signature produced (or merely observed on-chain, since it is public calldata) for association on one chain can be replayed verbatim on another chain to force an association there too.

### Finding Description
`associate()` derives the signer purely from `crypto.Keccak256Hash([]byte(customMessage))` with no chain-specific salt: [1](#0-0) 

This mirrors the identical pattern present in every legacy version of the precompile, e.g.: [2](#0-1) 

Once addresses are recovered, `associateAddresses` immediately calls into `AssociationHelper.AssociateAddresses`, which sets the address mapping and migrates any funds sitting in the deterministic cast address to the newly associated Sei address: [3](#0-2) [4](#0-3) 

The only guard against re-use is a check that the target Sei address is *not yet associated* on the executing chain: [5](#0-4) 
— but that check is per-chain state, not part of the signed hash. If the same account has not yet been associated on a *different* Sei-based chain (a very common state given Sei operates multiple networks with independent EVM address association state), the exact same `(v, r, s, customMessage)` tuple observed in a transaction on chain A can be resubmitted as calldata to the `associate()` precompile on chain B and will pass signature recovery and the "not yet associated" check there.

This is architecturally the same root cause the codebase explicitly documents and defends against for the EIP-7702 authorization path, where `RecoverAddressesFromAuthorization`/`AuthorityToPreAssociate` deliberately validates the authorization's own `ChainID` before honoring it, precisely to prevent "replaying a victim's public cross-chain authorization to force-associate them — migrating their direct-cast balance and orphaning staking/distribution state": [6](#0-5) [7](#0-6) 

No equivalent chain-ID check exists in the `customMessage`-based `associate()` precompile path.

### Impact Explanation
Forcing an association the victim did not intend on a given chain triggers `MigrateBalance`, which moves all `SpendableCoins` and wei balance sitting at the victim's direct-cast address to the newly-mapped Sei address, and can also remove/replace the cast account and its pubkey binding: [8](#0-7) 

Per the module's own documentation, cast vs. associated addresses represent genuinely different account views ("cast addresses will result in two views of the same account (e.g. two balances, etc.)"): [9](#0-8) 

An attacker replaying a signature the victim generated for association on one Sei chain, onto another Sei chain where the victim had not yet associated (e.g. still receiving funds at the cast address, or intentionally keeping the two identities separate), can force premature/unintended balance migration and address-mapping changes on that chain, without the victim's consent for that specific network. This matches the fund-relocation impact class called out for the (already-mitigated) EIP-7702 authority-replay case in this same codebase.

### Likelihood Explanation
The attack requires only a previously broadcast `associate()` call's public calldata (`v, r, s, customMessage`) from any chain, and a target chain/account state where that Sei address is not yet associated — a state that is easy to identify by simply querying `GetEVMAddress`/`GetSeiAddress` on the target chain. No special privileges are needed; any public RPC client can submit the replayed calldata as an EVM transaction to the `addr` precompile.

### Recommendation
Include the chain ID (and ideally a purpose-specific domain tag) in the hash that is signed for `associate()`, e.g.:
```go
customMessageHash := crypto.Keccak256Hash(
    []byte(customMessage),
    ctx.ChainID() bytes / big.Int chain id,
)
```
so a signature produced for one Sei chain cannot be replayed on another. This should be applied consistently to `precompiles/addr/addr.go` and all `precompiles/addr/legacy/*/addr.go` variants that build `customMessageHash` the same way, as well as `HandleAssociateTx`'s `AssociateTx` custom-message hashing path in `app/ante/evm_checktx.go`, which uses the identical construction: [10](#0-9) 

### Proof of Concept
1. On chain A (e.g. `atlantic-2` testnet), a user signs a `customMessage` (e.g. "Associate my Sei address") with their EVM private key and calls `associate(v, r, s, customMessage)` on the `addr` precompile to link their EVM/Sei addresses there. This transaction, including `v,r,s,customMessage`, is publicly visible on-chain.
2. An attacker copies the exact `(v, r, s, customMessage)` values.
3. On chain B (e.g. `pacific-1` mainnet), where the victim's Sei address is not yet associated, the attacker calls `associate(v, r, s, customMessage)` with the same parameters.
4. `crypto.Keccak256Hash([]byte(customMessage))` recomputes the identical hash (chain-independent), `helpers.GetAddresses` recovers the same `evmAddr`/`seiAddr`/`pubkey`, the "not yet associated" check on chain B passes, and `associateAddresses` executes — forcibly associating the victim's addresses and migrating any cast-address balance on chain B, without the victim ever submitting a transaction on chain B.

### Citations

**File:** precompiles/addr/addr.go (L169-202)
```go
	// v, r and s are components of a signature over the customMessage sent.
	// We use the signature to construct the user's pubkey to obtain their addresses.
	v := args[0].(string)
	r := args[1].(string)
	s := args[2].(string)
	customMessage := args[3].(string)

	rBytes, err := decodeHexString(r)
	if err != nil {
		return nil, 0, err
	}
	sBytes, err := decodeHexString(s)
	if err != nil {
		return nil, 0, err
	}
	vBytes, err := decodeHexString(v)
	if err != nil {
		return nil, 0, err
	}

	vBig := new(big.Int).SetBytes(vBytes)
	rBig := new(big.Int).SetBytes(rBytes)
	sBig := new(big.Int).SetBytes(sBytes)

	// Derive addresses
	vBig = new(big.Int).Add(vBig, utils.Big27)

	customMessageHash := crypto.Keccak256Hash([]byte(customMessage))
	evmAddr, seiAddr, pubkey, err := helpers.GetAddresses(vBig, rBig, sBig, customMessageHash)
	if err != nil {
		return nil, 0, err
	}

	return p.associateAddresses(ctx, method, evmAddr, seiAddr, pubkey)
```

**File:** precompiles/addr/legacy/v67/addr.go (L169-202)
```go
	}

	// v, r and s are components of a signature over the customMessage sent.
	// We use the signature to construct the user's pubkey to obtain their addresses.
	v := args[0].(string)
	r := args[1].(string)
	s := args[2].(string)
	customMessage := args[3].(string)

	rBytes, err := decodeHexString(r)
	if err != nil {
		return nil, 0, err
	}
	sBytes, err := decodeHexString(s)
	if err != nil {
		return nil, 0, err
	}
	vBytes, err := decodeHexString(v)
	if err != nil {
		return nil, 0, err
	}

	vBig := new(big.Int).SetBytes(vBytes)
	rBig := new(big.Int).SetBytes(rBytes)
	sBig := new(big.Int).SetBytes(sBytes)

	// Derive addresses
	vBig = new(big.Int).Add(vBig, utils.Big27)

	customMessageHash := crypto.Keccak256Hash([]byte(customMessage))
	evmAddr, seiAddr, pubkey, err := helpers.GetAddresses(vBig, rBig, sBig, customMessageHash)
	if err != nil {
		return nil, 0, err
	}
```

**File:** utils/helpers/associate.go (L34-55)
```go
func (p AssociationHelper) AssociateAddresses(ctx sdk.Context, seiAddr sdk.AccAddress, evmAddr common.Address, pubkey cryptotypes.PubKey, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if !castAddr.Equals(seiAddr) && p.accountKeeper.GetAccount(ctx, seiAddr) == nil {
		castAcc := p.accountKeeper.GetAccount(ctx, castAddr)
		castBaseAcc, ok := castAcc.(*authtypes.BaseAccount)
		if ok && castBaseAcc.GetPubKey() == nil && p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
			p.accountKeeper.SetAccount(ctx, authtypes.NewBaseAccount(seiAddr, pubkey, castBaseAcc.GetAccountNumber(), castBaseAcc.GetSequence()))
		}
	}
	p.evmKeeper.SetAddressMapping(ctx, seiAddr, evmAddr)
	acc := p.accountKeeper.GetAccount(ctx, seiAddr)
	if acc == nil {
		acc = p.accountKeeper.NewAccountWithAddress(ctx, seiAddr)
	}
	if acc.GetPubKey() == nil {
		if err := acc.SetPubKey(pubkey); err != nil {
			return err
		}
		p.accountKeeper.SetAccount(ctx, acc)
	}
	return p.MigrateBalance(ctx, evmAddr, seiAddr, migrateUseiOnly)
}
```

**File:** utils/helpers/associate.go (L57-83)
```go
func (p AssociationHelper) MigrateBalance(ctx sdk.Context, evmAddr common.Address, seiAddr sdk.AccAddress, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if castAddr.Equals(seiAddr) {
		return nil
	}
	var castAddrBalances sdk.Coins
	if migrateUseiOnly {
		castAddrBalances = sdk.Coins{p.bankKeeper.GetBalance(ctx, castAddr, "usei")}
	} else {
		castAddrBalances = p.bankKeeper.SpendableCoins(ctx, castAddr)
	}
	if !castAddrBalances.IsZero() {
		if err := p.bankKeeper.SendCoins(ctx, castAddr, seiAddr, castAddrBalances); err != nil {
			return err
		}
	}
	castAddrWei := p.bankKeeper.GetWeiBalance(ctx, castAddr)
	if !castAddrWei.IsZero() {
		if err := p.bankKeeper.SendCoinsAndWei(ctx, castAddr, seiAddr, sdk.ZeroInt(), castAddrWei); err != nil {
			return err
		}
	}
	if p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
		p.accountKeeper.RemoveAccount(ctx, authtypes.NewBaseAccountWithAddress(castAddr))
	}
	return nil
}
```

**File:** precompiles/addr/legacy/v601/addr.go (L178-182)
```go
	vBytes, err := decodeHexString(v)
	if err != nil {
		return nil, 0, err
	}

```

**File:** utils/helpers/address.go (L143-156)
```go
// AuthorityToPreAssociate returns the authority of an EIP-7702 authorization that should be
// associated with its true (pubkey-derived) Sei address before execution, or ok=false.
//
// It mirrors go-ethereum's StateTransition.validateAuthorization (chain id, nonce overflow,
// authority code, and account-nonce checks) so that pre-association happens only for
// authorizations the EVM will actually apply, and additionally skips authorities that are
// already associated. Mirroring validateAuthorization is essential to security: the
// authorization sig hash is computed from the auth's own ChainID, so recovery and
// auth.Authority() succeed for an authorization signed for ANY chain. Without these checks a
// publicly-visible authorization a user signed for another chain (e.g. Ethereum mainnet)
// could be replayed in a sponsored Sei SetCode tx to force-associate them — migrating their
// direct-cast balance and orphaning staking/distribution state — even though the EVM skips
// the wrong-chain authorization and installs no delegation.
func AuthorityToPreAssociate(ctx sdk.Context, k AuthorizationStateReader, auth ethtypes.SetCodeAuthorization) (common.Address, sdk.AccAddress, cryptotypes.PubKey, bool) {
```

**File:** x/evm/ante/preprocess.go (L103-110)
```go
	// EIP-7702 authorization authorities are distinct accounts from the tx sender, so the
	// sender association above does not cover them. Associate each authority to its true
	// (pubkey-derived) Sei address before EVM execution installs delegation code for it.
	// Otherwise SetCode creates a mutable direct-cast EVM->Sei mapping that a later
	// associatePubKey call can remap, orphaning any staking/distribution state created
	// under the direct-cast identity (which can then halt the chain via the distribution
	// validator-removal hook).
	p.associateAuthorizationAuthorities(ctx, msg, associateHelper)
```

**File:** x/evm/AGENTS.md (L9-16)
```markdown
## Dual Address Model

Every account can have both a Sei (bech32) address and an EVM (hex) address. The module maintains a bidirectional mapping between them.

- **Explicit association** — users can link their addresses via an Associate transaction or by signing any Cosmos/EVM transaction (the EVM address is derived from their secp256k1 public key).
- **Default (cast) addresses** — when no explicit association exists, the module falls back to a deterministic byte-cast. Cast addresses have limitations on receiving funds compared to fully associated addresses.

- **Why** - cast addresses will result in two views of the same account (e.g. two balances, etc.)
```

**File:** app/ante/evm_checktx.go (L182-190)
```go
func HandleAssociateTx(ctx sdk.Context, ek *evmkeeper.Keeper, atx *ethtx.AssociateTx, readOnly bool) (sdk.Context, error) {
	V, R, S := atx.GetRawSignatureValues()
	V = new(big.Int).Add(V, utils.Big27)
	// Hash custom message passed in
	customMessageHash := crypto.Keccak256Hash([]byte(atx.CustomMessage))
	evmAddr, seiAddr, seiPubkey, err := helpers.GetAddresses(V, R, S, customMessageHash)
	if err != nil {
		return ctx, err
	}
```
