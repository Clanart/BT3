Looking at the external report, the analog pattern is: a critical verification error is silently swallowed because a secondary operation in the error-handling branch overwrites the error variable, causing the function to return success when it should return failure.

Let me search for this specific pattern in the proof-facts validation path.