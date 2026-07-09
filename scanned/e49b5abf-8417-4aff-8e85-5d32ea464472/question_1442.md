# Q1442: NEAR EVM eNear interface path asset-branch confusion on finalization

## Question
Can an unprivileged attacker use `legacy/public eNEAR mint/burn/finalize flows` to make `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` release value through a more favorable branch than the source event actually authorized because of legacy proxy routes proof-validated Near outcomes into an older eNEAR mint/burn interface that still interacts with live bridge state, violating `legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions`?

## Target
- File/function: `evm/src/eNear/contracts/IENear.sol and ENearProxy usage`
- Entrypoint: `legacy/public eNEAR mint/burn/finalize flows`
- Attacker controls: proof bytes, receipt ids, token address, amount, and pause state
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches.
- Invariant to test: legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state.
