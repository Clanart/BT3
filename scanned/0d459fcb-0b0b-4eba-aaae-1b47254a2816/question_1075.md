# Q1075: EVM ENearProxy mint asset-branch confusion on finalization through cross-module drift

## Question
Can an unprivileged attacker use `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows` with control over recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state and desynchronize `evm/src/eNear/contracts/ENearProxy.sol::mint` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `asset-branch confusion on finalization` attack class because fabricates proof bytes around `currentReceiptId`, increments the stored receipt id, and calls `eNear.finaliseNearToEthTransfer` to mint legacy eNEAR, violating `legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::mint`
- Entrypoint: `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows`
- Attacker controls: recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Also assert cross-module consistency between `evm/src/eNear/contracts/ENearProxy.sol::mint` and the adjacent replay-protection bookkeeping after every branch.
