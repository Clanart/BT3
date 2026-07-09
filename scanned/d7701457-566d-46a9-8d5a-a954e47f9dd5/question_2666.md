# Q2666: Solana InitTransfer::process native fee and token fee drawn from wrong asset bucket

## Question
Can an unprivileged attacker use `public outbound flow through `init_transfer`` to make `solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs::process` pay native fee and token fee from inconsistent custody pools because of routes an outbound transfer through native-vault custody or bridged-burn semantics and posts the serialized message to Wormhole/Near, violating `the posted payload must remain exactly backed by the consumed token or SOL value regardless of which branch executes`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs::process`
- Entrypoint: `public outbound flow through `init_transfer``
- Attacker controls: mint/vault branch choice, user signer, token account contents, amount, fee, native fee, recipient string, and message
- Exploit idea: Focus on branches that mint native-fee tokens, transfer escrowed tokens, or unwrap wrapped native assets.
- Invariant to test: the posted payload must remain exactly backed by the consumed token or SOL value regardless of which branch executes
- Expected Immunefi impact: Balance manipulation
- Fast validation: Trace fee asset origin across every branch and assert that each fee component comes from the asset pool the bridge actually consumed.
