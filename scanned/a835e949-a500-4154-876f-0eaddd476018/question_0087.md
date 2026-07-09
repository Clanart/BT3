# Q87: Solana InitTransfer::process burn or lock before irreversible state

## Question
Can an unprivileged attacker use `public outbound flow through `init_transfer`` to force `solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs::process` to burn or lock assets before the transfer record becomes safely irreversible, and then recover or redirect the bridge flow via routes an outbound transfer through native-vault custody or bridged-burn semantics and posts the serialized message to Wormhole/Near, violating `the posted payload must remain exactly backed by the consumed token or SOL value regardless of which branch executes`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs::process`
- Entrypoint: `public outbound flow through `init_transfer``
- Attacker controls: mint/vault branch choice, user signer, token account contents, amount, fee, native fee, recipient string, and message
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed.
- Invariant to test: the posted payload must remain exactly backed by the consumed token or SOL value regardless of which branch executes
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped.
