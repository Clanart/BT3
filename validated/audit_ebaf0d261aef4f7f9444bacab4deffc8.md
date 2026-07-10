Let me search for the relevant code to analyze this exploit question.

Let me look at the NEAR `init_transfer_internal` and how it normalizes amounts before sending to Solana, plus the Solana `finalize_transfer` side.

Let me look at the `sign_transfer` function in NEAR and how it handles decimal normalization for NEAR-to-Solana transfers.