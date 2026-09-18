### Title
Use of `transfer()` for native SEI withdrawal in the canonical WSEI (Wrapped SEI) contract can permanently strand funds for smart-contract-wallet holders - ([File: contracts/src/WSEI.sol])

### Summary
The canonical Wrapped-SEI contract shipped and compiled into the chain's EVM artifacts (`x/evm/artifacts/wsei`) implements `withdraw()` using the low-level Solidity `transfer()` call, which forwards a fixed 2300-gas stipend to the receiver.

### Finding Description
`WSEI.sol`'s `withdraw` function unwraps native SEI by sending it directly with `payable(msg.sender).transfer(wad)`: [1](#0-0) 

This source contract is compiled and embedded as the production Wrapped-SEI bytecode/ABI under `x/evm/artifacts/wsei/WSEI.bin` and `WSEI.abi`, loaded via `GetBin()`/`GetParsedABI()`: [2](#0-1) 

Any unprivileged user can deploy this exact bytecode on-chain through the chain's own CLI command `seid tx evm deploy-wsei`, implemented in `CmdDeployWSEI`, which reads `wsei.GetBin()` and submits it as EVM contract-creation transaction data: [3](#0-2) 

Because `transfer()` hard-codes a 2300 gas stipend, any `msg.sender` that is a smart-contract wallet (e.g. a Gnosis-Safe-style multisig, an ERC-4337 account, or any contract with non-trivial logic in `receive()`/`fallback()`) will have its `withdraw()` call revert whenever its receive logic needs more than 2300 gas. Since `balanceOf[msg.sender] -= wad` happens before the `transfer()` call in the same transaction, the revert unwinds the whole call, meaning the wrapped balance can never be redeemed by that wallet using this code path — the SEI backing the wrapped tokens is permanently unreachable through the contract's public interface for that class of caller.

### Impact Explanation
Any smart-contract wallet or protocol contract that holds WSEI and attempts to unwrap it via `withdraw()` will have every such transaction revert, permanently freezing its wrapped SEI inside the WSEI contract with no alternative on-chain redemption path in the contract itself. This matches the "permanent freezing of funds" acceptance criterion, and because WSEI is the officially embedded/distributed wrapped-native-asset contract for the chain (referenced by CLI tooling and RPC test fixtures), the blast radius includes any integrator that deploys or relies on this canonical bytecode.

### Likelihood Explanation
Likelihood is moderate-to-high: WSEI is meant to be broadly used as the standard wrapped-native token for DeFi integrations on Sei EVM, and smart-contract wallets/multisigs are common counterparties in DeFi. Any such contract that deposits SEI and later tries to withdraw via `withdraw()` will hit this failure deterministically, not probabilistically.

### Recommendation
Replace `payable(msg.sender).transfer(wad)` in `WSEI.sol`'s `withdraw()` with a low-level `call` that forwards all available gas and checks the return value, e.g.:
```solidity
(bool success, ) = msg.sender.call{value: wad}("");
require(success, "SEI transfer failed");
```
and add reentrancy protection (checks-effects-interactions is already followed since balance is decremented first, but consider a reentrancy guard if additional logic is added later).

### Proof of Concept
1. Deploy a smart-contract wallet whose `receive()` function performs any action consuming more than 2300 gas (e.g., writing to storage, emitting an event with several indexed args, or making a downstream call).
2. From that contract, call `WSEI.deposit()` with some SEI value to mint WSEI balance.
3. From that contract, call `WSEI.withdraw(wad)`.
4. The internal `payable(msg.sender).transfer(wad)` call runs out of gas inside the wallet's `receive()`, causing the entire `withdraw` transaction to revert.
5. The smart-contract wallet's WSEI balance remains locked with no way to redeem it via the contract's public `withdraw()` function, since this failure is deterministic and always reproduces.

### Citations

**File:** contracts/src/WSEI.sol (L27-32)
```text
    function withdraw(uint wad) public {
        require(balanceOf[msg.sender] >= wad);
        balanceOf[msg.sender] -= wad;
        payable(msg.sender).transfer(wad);
        emit Withdrawal(msg.sender, wad);
    }
```

**File:** x/evm/artifacts/wsei/artifacts.go (L17-37)
```go
var cachedBin []byte
var cachedABI *abi.ABI

func GetABI() []byte {
	bz, err := f.ReadFile("WSEI.abi")
	if err != nil {
		panic("failed to read WSEI contract ABI")
	}
	return bz
}

func GetParsedABI() *abi.ABI {
	if cachedABI != nil {
		return cachedABI
	}
	parsedABI, err := abi.JSON(strings.NewReader(string(GetABI())))
	if err != nil {
		panic(err)
	}
	cachedABI = &parsedABI
	return cachedABI
```

**File:** x/evm/client/cli/tx.go (L428-463)
```go
func CmdDeployWSEI() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "deploy-wsei --from=<sender> --gas-fee-cap=<cap> --gas-limt=<limit> --evm-rpc=<url>",
		Short: "Deploy ERC20 contract for a native Sei token",
		Long:  "",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) (err error) {
			contractData := wsei.GetBin()

			key, err := getPrivateKey(cmd)
			if err != nil {
				return err
			}

			rpc, err := cmd.Flags().GetString(FlagRPC)
			if err != nil {
				return err
			}
			var nonce uint64
			if n, err := cmd.Flags().GetInt64(FlagNonce); err == nil && n >= 0 {
				nonce = uint64(n)
			} else {
				nonce, err = getNonce(rpc, key.PublicKey)
				if err != nil {
					return err
				}
			}

			txData, err := getTxData(cmd)
			if err != nil {
				return err
			}
			txData.Nonce = nonce
			txData.Value = utils.Big0
			txData.Data = contractData

```
